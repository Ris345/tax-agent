using Amazon.CDK;

namespace W2TextractCdk;

sealed class Program
{
    public static void Main()
    {
        var app = new App();

        var env = new Amazon.CDK.Environment
        {
            Account = System.Environment.GetEnvironmentVariable("CDK_DEFAULT_ACCOUNT"),
            Region  = System.Environment.GetEnvironmentVariable("CDK_DEFAULT_REGION"),
        };

        // Cognito User Pool — deploy first; outputs are read by the app's .env
        _ = new CognitoStack(app, "CognitoStack", new StackProps
        {
            Description = "Cognito User Pool + Hosted UI for Tax Agent authentication",
            Env = env,
        });

        var baseStack = new W2TextractStack(app, "W2TextractStack", new StackProps
        {
            Description = "W-2 Textract QUERIES pipeline — KMS-encrypted S3 bucket, Lambda analyser, DLQ, DynamoDB",
            Env = env,
        });

        // Step Functions orchestration — depends on W2TextractStack exports
        var pipelineStack = new TaxPipelineStack(app, "TaxPipelineStack", new StackProps
        {
            Description = "Step Functions state machine: Textract → Confidence Check → Claude → Validate → DynamoDB → PDF",
            Env = env,
        });
        pipelineStack.AddDependency(baseStack);

        app.Synth();
    }
}
