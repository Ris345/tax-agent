using Amazon.CDK;
using Amazon.CDK.AWS.DynamoDB;
using Amazon.CDK.AWS.Events;
using Amazon.CDK.AWS.Events.Targets;
using Amazon.CDK.AWS.IAM;
using Amazon.CDK.AWS.KMS;
using Amazon.CDK.AWS.Lambda;
using Amazon.CDK.AWS.Logs;
using Amazon.CDK.AWS.S3;
using Amazon.CDK.AWS.SNS;
using Amazon.CDK.AWS.StepFunctions;
using Constructs;
using System.Collections.Generic;
using System.IO;

namespace W2TextractCdk;

/// <summary>
/// Step Functions orchestration stack for the full tax document pipeline:
///   S3 PutObject → EventBridge → Step Functions
///     → ExtractWithTextract
///     → CheckConfidence (Choice)
///     → ClaudeFallback (if needed)
///     → ValidateDocument (Pydantic)
///     → StoreInDynamoDB
///     → GeneratePDF
///     → PipelineSucceeded / PipelineFailed
///
/// Prerequisites (exported from W2TextractStack):
///   W2TextractKmsKeyArn, W2TextractBucketName, TaxDocumentsTableName, TaxDocumentsTableArn
///
/// Deploy:
///   cd cdk
///   cdk deploy TaxPipelineStack
/// </summary>
public sealed class TaxPipelineStack : Stack
{
    public TaxPipelineStack(Construct scope, string id, IStackProps? props = null)
        : base(scope, id, props)
    {
        // ── Import shared resources from W2TextractStack ──────────────────────
        var kmsKeyArn = Fn.ImportValue("W2TextractKmsKeyArn");
        var kmsKey    = Key.FromKeyArn(this, "SharedKmsKey", kmsKeyArn);

        var bucketName = Fn.ImportValue("W2TextractBucketName");
        var bucket     = Bucket.FromBucketAttributes(this, "UploadBucket",
            new BucketAttributes { BucketName = bucketName, EncryptionKey = kmsKey });

        var tableArn  = Fn.ImportValue("TaxDocumentsTableArn");
        var tableName = Fn.ImportValue("TaxDocumentsTableName");
        var table     = Table.FromTableAttributes(this, "TaxDocumentsTable",
            new TableAttributes
            {
                TableArn          = tableArn,
                EncryptionKey     = kmsKey,
            });

        var storageKmsKeyArn = kmsKeyArn;

        // ── Context overrides ─────────────────────────────────────────────────
        var confidenceThreshold = Node.TryGetContext("confidenceThreshold") as string ?? "85.0";

        // ── Optional SNS alert topic ──────────────────────────────────────────
        var alertTopic = new Topic(this, "PipelineAlertTopic", new TopicProps
        {
            TopicName   = "tax-pipeline-errors",
            MasterKey   = kmsKey,
            DisplayName = "Tax pipeline failure alerts",
        });

        // ── Common Lambda settings ────────────────────────────────────────────
        var sharedEnv = new Dictionary<string, string>
        {
            ["LOG_LEVEL"]               = "INFO",
            ["CONFIDENCE_THRESHOLD"]    = confidenceThreshold,
            ["TAX_DOCS_TABLE_NAME"]     = tableName,
            ["TAX_STORAGE_KMS_KEY_ARN"] = storageKmsKeyArn,
        };

        // ── Helper: create a Lambda function for the pipeline ─────────────────
        Function MakeLambda(
            string id,
            string name,
            string handler,
            string assetPath,
            int    timeoutSecs = 30,
            int    memorySizeMb = 256,
            Dictionary<string, string>? extraEnv = null)
        {
            var env = new Dictionary<string, string>(sharedEnv);
            if (extraEnv != null)
                foreach (var kv in extraEnv) env[kv.Key] = kv.Value;

            var fn = new Function(this, id, new FunctionProps
            {
                FunctionName = name,
                Runtime      = Runtime.PYTHON_3_12,
                Handler      = handler,
                Code         = Code.FromAsset(assetPath),
                Timeout      = Duration.Seconds(timeoutSecs),
                MemorySize   = memorySizeMb,
                Environment  = env,
            });

            _ = new LogGroup(this, id + "Logs", new Amazon.CDK.AWS.Logs.LogGroupProps
            {
                LogGroupName  = $"/aws/lambda/{name}",
                Retention     = RetentionDays.ONE_MONTH,
                RemovalPolicy = RemovalPolicy.DESTROY,
            });

            return fn;
        }

        // ── Lambda functions ──────────────────────────────────────────────────

        var textractFn = MakeLambda(
            "TextractOnlyFn", "tax-pipeline-textract", "handler.handler",
            "../lambda/textract_only", timeoutSecs: 60, memorySizeMb: 256);

        textractFn.AddToRolePolicy(new PolicyStatement(new PolicyStatementProps
        {
            Sid       = "TextractAnalyze",
            Actions   = new[] { "textract:AnalyzeDocument" },
            Resources = new[] { "*" },
        }));
        bucket.GrantRead(textractFn);
        kmsKey.GrantDecrypt(textractFn);

        var validatorFn = MakeLambda(
            "ValidatorFn", "tax-pipeline-validator", "handler.handler",
            "../lambda/validator", timeoutSecs: 30, memorySizeMb: 256);

        var storeFn = MakeLambda(
            "DynamoDBStoreFn", "tax-pipeline-store", "handler.handler",
            "../lambda/dynamodb_store", timeoutSecs: 30, memorySizeMb: 256);

        table.GrantReadWriteData(storeFn);
        kmsKey.GrantDecrypt(storeFn);
        kmsKey.GrantEncryptDecrypt(storeFn);  // AWS Encryption SDK needs GenerateDataKey too

        var pdfFn = MakeLambda(
            "PDFGeneratorFn", "tax-pipeline-pdf-generator", "handler.handler",
            "../lambda/pdf_generator", timeoutSecs: 30, memorySizeMb: 512,
            extraEnv: new Dictionary<string, string>
            {
                ["PRESIGNED_URL_EXPIRY_SECONDS"] = "3600",
            });

        bucket.GrantReadWrite(pdfFn);
        kmsKey.GrantEncryptDecrypt(pdfFn);

        var errorFn = MakeLambda(
            "ErrorHandlerFn", "tax-pipeline-error-handler", "handler.handler",
            "../lambda/error_handler", timeoutSecs: 15, memorySizeMb: 128,
            extraEnv: new Dictionary<string, string>
            {
                ["PIPELINE_ERROR_SNS_TOPIC_ARN"] = alertTopic.TopicArn,
            });

        alertTopic.GrantPublish(errorFn);

        // ── Step Functions execution role ─────────────────────────────────────
        var sfnRole = new Role(this, "StateMachineRole", new RoleProps
        {
            RoleName  = "tax-pipeline-sfn-role",
            AssumedBy = new ServicePrincipal("states.amazonaws.com"),
        });

        // Allow the state machine to invoke each Lambda
        foreach (var fn in new[] { textractFn, validatorFn, storeFn, pdfFn, errorFn })
        {
            fn.GrantInvoke(sfnRole);
        }

        // ── State machine ─────────────────────────────────────────────────────
        var aslPath = Path.GetFullPath("../infrastructure/statemachine/tax_pipeline.asl.json");
        var aslBody = File.ReadAllText(aslPath);

        var sfnLogGroup = new LogGroup(this, "StateMachineLogGroup", new Amazon.CDK.AWS.Logs.LogGroupProps
        {
            LogGroupName  = "/aws/states/tax-pipeline",
            Retention     = RetentionDays.ONE_MONTH,
            RemovalPolicy = RemovalPolicy.DESTROY,
        });

        // L2 StateMachine — handles IAM grants, log group resource policy, and
        // dependency ordering automatically, avoiding the IAM eventual-consistency
        // failure that plagued the previous CfnStateMachine (L1) approach.
        var stateMachine = new StateMachine(this, "TaxPipelineStateMachine",
            new StateMachineProps
            {
                StateMachineName = "tax-document-pipeline",
                StateMachineType = StateMachineType.STANDARD,
                Role             = sfnRole,
                // CDK DefinitionSubstitutions replaces ${...} tokens in the ASL at deploy time
                DefinitionBody          = DefinitionBody.FromString(aslBody),
                DefinitionSubstitutions = new Dictionary<string, string>
                {
                    ["TextractFunctionArn"]     = textractFn.FunctionArn,
                    ["ValidatorFunctionArn"]    = validatorFn.FunctionArn,
                    ["StoreFunctionArn"]        = storeFn.FunctionArn,
                    ["PDFGeneratorFunctionArn"] = pdfFn.FunctionArn,
                    ["ErrorHandlerFunctionArn"] = errorFn.FunctionArn,
                },
                Logs = new LogOptions
                {
                    Destination          = sfnLogGroup,
                    Level                = LogLevel.ERROR,
                    IncludeExecutionData = true,
                },
                TracingEnabled = true,
            });

        // ── EventBridge rule: S3 PutObject → Step Functions ───────────────────
        // EventBridge S3 notifications require the source bucket to have
        // EventBridge notifications enabled (set in W2TextractStack or here).
        // No explicit Role — CDK creates the correct events.amazonaws.com role
        // automatically. Passing the SFN execution role here was a bug since
        // that role trusts states.amazonaws.com, not events.amazonaws.com.
        var sfnTarget = new SfnStateMachine(
            stateMachine,
            new SfnStateMachineProps
            {
                // Transform the EventBridge event into the Step Functions input shape.
                // document_type is intentionally omitted — the Textract Lambda derives
                // it authoritatively from the S3 key (uploads/{userId}/{date}/{docType}/...).
                Input = RuleTargetInput.FromObject(new Dictionary<string, object>
                {
                    ["bucket"]         = EventField.FromPath("$.detail.bucket.name"),
                    ["key"]            = EventField.FromPath("$.detail.object.key"),
                    ["user_id"]        = EventField.FromPath("$.detail.object.key"),
                    ["correlation_id"] = EventField.FromPath("$.id"),
                }),
            });

        _ = new Rule(this, "S3UploadRule", new RuleProps
        {
            RuleName    = "tax-pipeline-s3-trigger",
            Description = "Trigger tax pipeline on S3 PutObject under uploads/ prefix",
            EventPattern = new EventPattern
            {
                Source      = new[] { "aws.s3" },
                DetailType  = new[] { "Object Created" },
                Detail      = new Dictionary<string, object>
                {
                    ["bucket"] = new Dictionary<string, object>
                    {
                        ["name"] = new[] { bucketName },
                    },
                    ["object"] = new Dictionary<string, object>
                    {
                        // EventBridge requires prefix as an array of matcher objects,
                        // not a plain string — e.g. [{"prefix": "uploads/"}]
                        ["key"] = new object[] { new Dictionary<string, string> { ["prefix"] = "uploads/" } },
                    },
                },
            },
            Targets = new IRuleTarget[] { sfnTarget },
        });

        // ── Outputs ───────────────────────────────────────────────────────────
        _ = new CfnOutput(this, "StateMachineArn", new CfnOutputProps
        {
            Description = "Tax pipeline state machine ARN",
            Value       = stateMachine.StateMachineArn,
            ExportName  = "TaxPipelineStateMachineArn",
        });

        _ = new CfnOutput(this, "AlertTopicArn", new CfnOutputProps
        {
            Description = "SNS topic for pipeline failure alerts — subscribe your email here",
            Value       = alertTopic.TopicArn,
            ExportName  = "TaxPipelineAlertTopicArn",
        });
    }
}
