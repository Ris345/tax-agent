using Amazon.CDK;
using Amazon.CDK.AWS.IAM;
using Amazon.CDK.AWS.KMS;
using Amazon.CDK.AWS.Lambda;
using Amazon.CDK.AWS.Logs;
using Amazon.CDK.AWS.S3;
using Amazon.CDK.AWS.S3.Notifications;
using Amazon.CDK.AWS.SecretsManager;
using Amazon.CDK.AWS.DynamoDB;
using Amazon.CDK.AWS.SQS;
using Constructs;

namespace W2TextractCdk;

/// <summary>
/// Full W-2 processing pipeline:
///   KMS key → private S3 bucket (SSE-KMS)
///   → Lambda (Textract QUERIES + Claude Sonnet 4 vision fallback)
///   → FIFO DLQ
///
/// The Anthropic API key is stored in Secrets Manager and fetched at cold-start.
/// It is never written into the Lambda environment configuration in plain text.
///
/// Deploy:
///   cd cdk
///   cdk deploy --context confidenceThreshold=85 \
///              --context claudeConfidenceThreshold=80
///
/// Populate the secret after first deploy:
///   aws secretsmanager put-secret-value \
///     --secret-id tax-agent/anthropic-api-key \
///     --secret-string '{"ANTHROPIC_API_KEY":"sk-ant-..."}'
///
/// To reuse an existing S3 bucket:
///   cdk deploy --context existingBucketName=my-upload-bucket
/// </summary>
public sealed class W2TextractStack : Stack
{
    public W2TextractStack(Construct scope, string id, IStackProps? props = null)
        : base(scope, id, props)
    {
        // ── Context values ────────────────────────────────────────────────────
        var confidenceThreshold       = Node.TryGetContext("confidenceThreshold")       as string ?? "85.0";
        var claudeConfidenceThreshold = Node.TryGetContext("claudeConfidenceThreshold") as string ?? "80.0";
        var claudeModel               = Node.TryGetContext("claudeModel")               as string ?? "claude-sonnet-4-20250514";
        var existingBucketName        = Node.TryGetContext("existingBucketName")        as string;

        // ── KMS key ───────────────────────────────────────────────────────────
        var kmsKey = new Key(this, "W2KmsKey", new KeyProps
        {
            Description       = "SSE-KMS key for W-2 document storage",
            EnableKeyRotation = true,
            RemovalPolicy     = RemovalPolicy.RETAIN,
            Alias             = "alias/tax-agent-w2",
        });

        // ── Secrets Manager: Anthropic API key ────────────────────────────────
        // The secret value is intentionally left empty here — populate it out-of-band
        // with `aws secretsmanager put-secret-value` after the first `cdk deploy`.
        // The Python code reads it at Lambda cold-start via boto3.
        var anthropicSecret = new Amazon.CDK.AWS.SecretsManager.Secret(
            this, "AnthropicApiKeySecret",
            new SecretProps
            {
                SecretName  = "tax-agent/anthropic-api-key",
                Description = "Anthropic API key for W-2 Claude Sonnet 4 vision fallback",
                // Encrypt the secret with the same KMS key used for S3 so a single
                // key policy controls access to both the documents and the API key.
                EncryptionKey = kmsKey,
            });

        // ── Dead-letter queue ─────────────────────────────────────────────────
        var dlq = new Queue(this, "W2ProcessingDlq", new QueueProps
        {
            QueueName                 = "w2-textract-dlq.fifo",
            Fifo                      = true,
            ContentBasedDeduplication = true,
            RetentionPeriod           = Duration.Days(14),
            Encryption                = QueueEncryption.KMS_MANAGED,
        });

        // ── DynamoDB: tax document storage ───────────────────────────────────
        // PK: user_id (String)  SK: doc_id (String, prefix = "W2#2024#<uuid4>")
        // TTL:  expires_at (Unix epoch integer — documents auto-expire after 1 year)
        // Encryption: customer-managed KMS (same key as S3 / Secrets Manager)
        var taxDocsTable = new Table(this, "TaxDocumentsTable", new TableProps
        {
            TableName         = $"tax-documents-{Account}-{Region}",
            PartitionKey      = new Attribute { Name = "user_id", Type = AttributeType.STRING },
            SortKey           = new Attribute { Name = "doc_id",  Type = AttributeType.STRING },
            BillingMode       = BillingMode.PAY_PER_REQUEST,
            PointInTimeRecovery = true,
            Encryption        = TableEncryption.CUSTOMER_MANAGED,
            EncryptionKey     = kmsKey,
            TimeToLiveAttribute = "expires_at",
            RemovalPolicy     = RemovalPolicy.RETAIN,
        });

        // ── Lambda execution role ─────────────────────────────────────────────
        var lambdaRole = new Role(this, "W2LambdaRole", new RoleProps
        {
            RoleName  = "w2-textract-lambda-role",
            AssumedBy = new ServicePrincipal("lambda.amazonaws.com"),
            ManagedPolicies = new[]
            {
                ManagedPolicy.FromAwsManagedPolicyName(
                    "service-role/AWSLambdaBasicExecutionRole"),
            },
        });

        // Textract: resource-level conditions are not supported for AnalyzeDocument
        lambdaRole.AddToPolicy(new PolicyStatement(new PolicyStatementProps
        {
            Sid       = "TextractAnalyzeDocument",
            Effect    = Effect.ALLOW,
            Actions   = new[] { "textract:AnalyzeDocument" },
            Resources = new[] { "*" },
        }));

        // KMS: decrypt SSE-KMS objects (needed by both S3 GetObject and Textract)
        lambdaRole.AddToPolicy(new PolicyStatement(new PolicyStatementProps
        {
            Sid       = "KmsDecryptW2Documents",
            Effect    = Effect.ALLOW,
            Actions   = new[] { "kms:Decrypt", "kms:GenerateDataKey" },
            Resources = new[] { kmsKey.KeyArn },
        }));

        // Secrets Manager: read the Anthropic API key at cold-start
        // GetSecretValue is the only operation the Lambda needs.
        lambdaRole.AddToPolicy(new PolicyStatement(new PolicyStatementProps
        {
            Sid       = "ReadAnthropicApiKey",
            Effect    = Effect.ALLOW,
            Actions   = new[] { "secretsmanager:GetSecretValue" },
            Resources = new[] { anthropicSecret.SecretArn },
        }));

        // SQS: write failed invocations to DLQ
        dlq.GrantSendMessages(lambdaRole);

        // DynamoDB: PutItem, GetItem, Query, DeleteItem, ConditionCheckItem …
        taxDocsTable.GrantReadWriteData(lambdaRole);

        // KMS: DynamoDB encrypts items with the same CMK; the key policy grants already
        // issued above (kms:Decrypt + kms:GenerateDataKey) cover this access path too.

        // ── Lambda function ───────────────────────────────────────────────────
        var fn = new Function(this, "W2TextractFunction", new FunctionProps
        {
            FunctionName  = "w2-textract-analyzer",
            Description   = "W-2 pipeline: Textract QUERIES → Claude Sonnet 4 vision fallback for low-confidence fields",
            Runtime       = Runtime.PYTHON_3_12,
            Handler       = "handler.handler",
            Code          = Code.FromAsset("../lambda/textract_w2"),
            Role          = lambdaRole,
            // Breakdown of the 120 s budget:
            //   Textract AnalyzeDocument : ~10–30 s
            //   S3 GetObject (raw page)  : ~1–3 s
            //   Claude Sonnet 4 stream   : ~10–40 s (model + PDF size dependent)
            Timeout       = Duration.Seconds(120),
            MemorySize    = 512,
            RetryAttempts = 1,
            DeadLetterQueue = dlq,
            Environment   = new Dictionary<string, string>
            {
                // Textract confidence gate
                ["CONFIDENCE_THRESHOLD"]        = confidenceThreshold,
                // Claude confidence gate (applied after fallback)
                ["CLAUDE_CONFIDENCE_THRESHOLD"] = claudeConfidenceThreshold,
                // Model string passed through to claude_fallback.py
                ["CLAUDE_MODEL"]                = claudeModel,
                // Secret ARN read at cold-start; no plain-text key in env
                ["ANTHROPIC_SECRET_ARN"]        = anthropicSecret.SecretArn,
                // DynamoDB storage for extracted schemas + encrypted PII
                ["TAX_DOCS_TABLE_NAME"]         = taxDocsTable.TableName,
                // AWS Encryption SDK uses this CMK for field-level PII encryption
                ["TAX_STORAGE_KMS_KEY_ARN"]     = kmsKey.KeyArn,
                ["LOG_LEVEL"]                   = "INFO",
            },
        });

        // ── CloudWatch log group ──────────────────────────────────────────────
        _ = new LogGroup(this, "W2FunctionLogGroup", new LogGroupProps
        {
            LogGroupName  = $"/aws/lambda/{fn.FunctionName}",
            Retention     = RetentionDays.ONE_MONTH,
            RemovalPolicy = RemovalPolicy.DESTROY,
        });

        // ── S3 bucket ─────────────────────────────────────────────────────────
        IBucket bucket;

        if (existingBucketName is not null)
        {
            // Import an existing bucket managed by another stack (e.g. SAM template).
            // CDK creates a BucketNotifications custom resource to add the trigger.
            bucket = Bucket.FromBucketAttributes(this, "UploadBucket", new BucketAttributes
            {
                BucketName    = existingBucketName,
                EncryptionKey = kmsKey,  // override if the bucket uses a different key
            });
            bucket.GrantRead(lambdaRole);
        }
        else
        {
            var newBucket = new Bucket(this, "W2UploadBucket", new BucketProps
            {
                BucketName        = $"tax-agent-w2-{Account}-{Region}",
                BlockPublicAccess = BlockPublicAccess.BLOCK_ALL,
                PublicReadAccess  = false,
                Encryption        = BucketEncryption.KMS,
                EncryptionKey     = kmsKey,
                BucketKeyEnabled  = true,
                Versioned         = true,
                EnforceSSL        = true,
                Cors = new[]
                {
                    new CorsRule
                    {
                        AllowedOrigins = new[] { "http://localhost:3000" },
                        AllowedMethods = new[] { HttpMethods.POST, HttpMethods.GET, HttpMethods.HEAD },
                        AllowedHeaders = new[] { "*" },
                        ExposedHeaders = new[] { "ETag" },
                        MaxAge         = 3600,
                    },
                },
                RemovalPolicy = RemovalPolicy.RETAIN,
            });
            newBucket.GrantRead(lambdaRole);
            bucket = newBucket;
        }

        // Ensure the Lambda role can also decrypt via the KMS key path used
        // when reading the raw S3 object for the Claude fallback.
        kmsKey.GrantDecrypt(lambdaRole);

        // ── S3 → Lambda event notifications ──────────────────────────────────
        var destination = new LambdaDestination(fn);

        bucket.AddEventNotification(
            EventType.OBJECT_CREATED_PUT, destination,
            new NotificationKeyFilter { Prefix = "uploads/", Suffix = ".pdf" });

        foreach (var ext in new[] { ".jpg", ".jpeg", ".png", ".tiff", ".tif" })
        {
            bucket.AddEventNotification(
                EventType.OBJECT_CREATED_PUT, destination,
                new NotificationKeyFilter { Prefix = "uploads/", Suffix = ext });
        }

        fn.AddPermission("AllowS3Invoke", new Permission
        {
            Principal     = new ServicePrincipal("s3.amazonaws.com"),
            Action        = "lambda:InvokeFunction",
            SourceAccount = Account,
        });

        // ── Outputs ───────────────────────────────────────────────────────────
        _ = new CfnOutput(this, "LambdaArn", new CfnOutputProps
        {
            Description = "W-2 Textract + Claude analyser Lambda ARN",
            Value       = fn.FunctionArn,
            ExportName  = "W2TextractLambdaArn",
        });

        _ = new CfnOutput(this, "KmsKeyArn", new CfnOutputProps
        {
            Description = "KMS key ARN",
            Value       = kmsKey.KeyArn,
            ExportName  = "W2TextractKmsKeyArn",
        });

        _ = new CfnOutput(this, "BucketName", new CfnOutputProps
        {
            Description = "W-2 intake bucket name",
            Value       = bucket.BucketName,
            ExportName  = "W2TextractBucketName",
        });

        _ = new CfnOutput(this, "DlqArn", new CfnOutputProps
        {
            Description = "Dead-letter queue ARN for failed invocations",
            Value       = dlq.QueueArn,
            ExportName  = "W2TextractDlqArn",
        });

        _ = new CfnOutput(this, "AnthropicSecretArn", new CfnOutputProps
        {
            Description = "Secrets Manager ARN — populate with your Anthropic API key",
            Value       = anthropicSecret.SecretArn,
            ExportName  = "W2TextractAnthropicSecretArn",
        });

        _ = new CfnOutput(this, "TaxDocumentsTableName", new CfnOutputProps
        {
            Description = "DynamoDB table storing extracted tax document schemas",
            Value       = taxDocsTable.TableName,
            ExportName  = "TaxDocumentsTableName",
        });

        _ = new CfnOutput(this, "TaxDocumentsTableArn", new CfnOutputProps
        {
            Description = "DynamoDB table ARN (for cross-stack IAM grants)",
            Value       = taxDocsTable.TableArn,
            ExportName  = "TaxDocumentsTableArn",
        });
    }
}
