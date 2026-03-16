using Amazon.CDK;
using Amazon.CDK.AWS.Cognito;
using Amazon.CDK.AWS.KMS;
using Constructs;
using System.Collections.Generic;

namespace W2TextractCdk;

/// <summary>
/// Cognito User Pool for the Tax Agent web application.
///
/// Authentication model:
///   - Authorization Code + PKCE (confidential client)
///   - Hosted UI domain for sign-in / sign-up
///   - Self sign-up DISABLED — admin must create users
///   - MFA: OPTIONAL (TOTP supported; SMS not required)
///   - Access tokens: 1 hour  |  Refresh tokens: 30 days
///   - Token revocation enabled (explicit logout invalidates tokens)
///   - PreventUserExistenceErrors: ENABLED (security hardening)
///   - Advanced security mode: ENFORCED
///
/// After deploy:
///   1. Populate COGNITO_CLIENT_SECRET in .env.local (from the output below).
///   2. Create the first admin user:
///      aws cognito-idp admin-create-user \
///        --user-pool-id &lt;UserPoolId&gt; \
///        --username admin@example.com \
///        --user-attributes Name=email,Value=admin@example.com Name=email_verified,Value=true \
///        --temporary-password 'Change.Me.123!'
///
/// Deploy:
///   cd cdk
///   cdk deploy CognitoStack
/// </summary>
public sealed class CognitoStack : Stack
{
    // Exported values for other stacks / documentation.
    public string UserPoolId     { get; }
    public string ClientId       { get; }
    public string HostedUiDomain { get; }

    public CognitoStack(Construct scope, string id, IStackProps? props = null)
        : base(scope, id, props)
    {
        // ── Context ───────────────────────────────────────────────────────────
        // Pass callback URLs at deploy time:
        //   cdk deploy CognitoStack \
        //     --context callbackUrl=https://app.example.com/api/auth/callback \
        //     --context logoutUrl=https://app.example.com/login
        var callbackUrl = Node.TryGetContext("callbackUrl") as string
            ?? "http://localhost:3000/api/auth/callback";
        var logoutUrl = Node.TryGetContext("logoutUrl") as string
            ?? "http://localhost:3000/login";

        // Allow both localhost (dev) and prod URL.
        var callbackUrls = new List<string> { callbackUrl };
        var logoutUrls   = new List<string> { logoutUrl };
        if (!callbackUrl.StartsWith("http://localhost"))
        {
            callbackUrls.Add("http://localhost:3000/api/auth/callback");
            logoutUrls.Add("http://localhost:3000/login");
        }

        // ── KMS key for the User Pool (advanced security feature encryption) ─
        // Re-use the shared tax-agent KMS key exported by W2TextractStack if
        // available, otherwise create a dedicated key.
        var cognitoKmsKey = new Key(this, "CognitoKmsKey", new KeyProps
        {
            Description       = "Encryption key for Cognito advanced security data",
            EnableKeyRotation = true,
            RemovalPolicy     = RemovalPolicy.RETAIN,
            Alias             = "alias/tax-agent-cognito",
        });

        // ── User Pool ─────────────────────────────────────────────────────────
        var userPool = new UserPool(this, "TaxAgentUserPool", new UserPoolProps
        {
            UserPoolName = "tax-agent-users",

            // Self sign-up disabled — this is an internal tax tool.
            SelfSignUpEnabled = false,

            // Only admins can create users; send a temporary password via email.
            SignInAliases = new SignInAliases { Email = true, Username = false },
            AutoVerify    = new AutoVerifiedAttrs { Email = true },

            StandardAttributes = new StandardAttributes
            {
                Email = new StandardAttribute { Required = true, Mutable = true },
            },

            // Temporary passwords expire after 7 days.
            TemporaryPasswordValidity = Duration.Days(7),

            // Strong password policy.
            PasswordPolicy = new PasswordPolicy
            {
                MinLength        = 12,
                RequireLowercase = true,
                RequireUppercase = true,
                RequireDigits    = true,
                RequireSymbols   = true,
                TempPasswordValidity = Duration.Days(7),
            },

            // MFA optional — users can enrol a TOTP authenticator app.
            Mfa           = Mfa.OPTIONAL,
            MfaSecondFactor = new MfaSecondFactor { Otp = true, Sms = false },

            // Account recovery via email only (no SMS — avoids SIM-swap).
            AccountRecovery = AccountRecovery.EMAIL_ONLY,

            // Advanced security mode: Cognito risk-based adaptive authentication.
            // Flags impossible-travel, credential stuffing, etc.
            AdvancedSecurityMode = AdvancedSecurityMode.ENFORCED,

            // Don't reveal whether an account exists (prevents enumeration).
            PreventUserExistenceErrors = true,

            // Send email via Cognito's built-in SES integration (no custom SES needed).
            UserVerification = new UserVerificationConfig
            {
                EmailSubject = "Your Tax Agent verification code",
                EmailBody    = "Your Tax Agent verification code is {####}.",
                EmailStyle   = VerificationEmailStyle.CODE,
            },

            UserInvitation = new UserInvitationConfig
            {
                EmailSubject = "Your Tax Agent temporary password",
                EmailBody    = "Hello {username}, your temporary password is {####}. It expires in 7 days.",
            },

            RemovalPolicy = RemovalPolicy.RETAIN,
            CustomSenderKmsKey = cognitoKmsKey,
        });

        // ── Hosted UI domain ──────────────────────────────────────────────────
        // Domain prefix must be globally unique across all AWS Cognito pools.
        // Using account ID prevents collisions.
        var domain = userPool.AddDomain("HostedUiDomain", new UserPoolDomainOptions
        {
            CognitoDomain = new CognitoDomainOptions
            {
                DomainPrefix = $"tax-agent-{Account}",
            },
        });

        var hostedUiBaseUrl = domain.BaseUrl();

        // ── App Client (confidential, Authorization Code + PKCE) ──────────────
        var appClient = userPool.AddClient("WebAppClient", new UserPoolClientOptions
        {
            UserPoolClientName = "tax-agent-web",

            // Authorization Code grant only — no implicit, no client_credentials.
            AuthFlows = new AuthFlow
            {
                UserPassword      = false,  // no ALLOW_USER_PASSWORD_AUTH
                UserSrp           = false,  // no ALLOW_USER_SRP_AUTH
                AdminUserPassword = false,  // no ALLOW_ADMIN_USER_PASSWORD_AUTH
                Custom            = false,
            },

            OAuth = new OAuthSettings
            {
                Flows = new OAuthFlows
                {
                    AuthorizationCodeGrant = true,
                    ImplicitCodeGrant      = false,
                    ClientCredentials      = false,
                },
                Scopes = new[]
                {
                    OAuthScope.EMAIL,
                    OAuthScope.OPENID,
                    OAuthScope.PROFILE,
                },
                CallbackUrls = callbackUrls.ToArray(),
                LogoutUrls   = logoutUrls.ToArray(),
            },

            // Confidential client: generates a client secret for the server-side
            // token exchange in /api/auth/callback and /api/auth/refresh.
            GenerateSecret = true,

            // Token validity windows.
            AccessTokenValidity  = Duration.Hours(1),
            IdTokenValidity      = Duration.Hours(1),
            RefreshTokenValidity = Duration.Days(30),

            // Required with custom validity settings.
            EnableTokenRevocation    = true,
            PreventUserExistenceErrors = true,

            SupportedIdentityProviders = new[]
            {
                UserPoolClientIdentityProvider.COGNITO,
            },
        });

        // ── Outputs ───────────────────────────────────────────────────────────
        UserPoolId     = userPool.UserPoolId;
        ClientId       = appClient.UserPoolClientId;
        HostedUiDomain = hostedUiBaseUrl;

        _ = new CfnOutput(this, "UserPoolId", new CfnOutputProps
        {
            Description = "Cognito User Pool ID → COGNITO_USER_POOL_ID",
            Value       = userPool.UserPoolId,
            ExportName  = "TaxAgentUserPoolId",
        });

        _ = new CfnOutput(this, "ClientId", new CfnOutputProps
        {
            Description = "Cognito App Client ID → COGNITO_CLIENT_ID",
            Value       = appClient.UserPoolClientId,
            ExportName  = "TaxAgentCognitoClientId",
        });

        _ = new CfnOutput(this, "HostedUiDomain", new CfnOutputProps
        {
            Description = "Cognito Hosted UI base URL → COGNITO_DOMAIN",
            Value       = hostedUiBaseUrl,
            ExportName  = "TaxAgentCognitoHostedUiDomain",
        });

        _ = new CfnOutput(this, "ClientSecretNote", new CfnOutputProps
        {
            Description =
                "Retrieve the client secret: " +
                $"aws cognito-idp describe-user-pool-client " +
                $"--user-pool-id {userPool.UserPoolId} " +
                $"--client-id {appClient.UserPoolClientId} " +
                "--query UserPoolClient.ClientSecret --output text",
            Value = "See description — not printed here for security",
        });

        _ = new CfnOutput(this, "Region", new CfnOutputProps
        {
            Description = "AWS region → COGNITO_REGION",
            Value       = Region,
            ExportName  = "TaxAgentCognitoRegion",
        });
    }
}
