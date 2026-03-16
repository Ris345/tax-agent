# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Next.js App
```bash
npm run dev       # start dev server (localhost:3000)
npm run build     # production build
npm run lint      # ESLint via next lint
```

### CDK Infrastructure (C# .NET 8.0, run from `cdk/`)
```bash
cdk synth                    # synthesise CloudFormation templates
cdk deploy CognitoStack      # deploy auth stack first (outputs feed .env.local)
cdk deploy W2TextractStack   # deploy S3, KMS, DynamoDB, base Lambda
cdk deploy TaxPipelineStack  # deploy Step Functions + pipeline Lambdas

# Pass Cognito callback URLs at deploy time:
cdk deploy CognitoStack \
  --context callbackUrl=https://app.example.com/api/auth/callback \
  --context logoutUrl=https://app.example.com/login
```

### Python Lambda syntax check (no test framework configured)
```bash
python3 -c "import ast; ast.parse(open('lambda/path/handler.py').read())"
```

## Architecture

This is a **multi-layer monorepo**: a Next.js web app, AWS CDK infrastructure (C#), Python Lambda functions, and a Step Functions state machine.

### Upload → Pipeline Flow

1. **Browser** → `POST /api/upload` → Next.js generates an S3 presigned POST scoped to `uploads/{userId}/{date}/{uuid}.{ext}`.
2. Browser POSTs directly to S3. Then calls `POST /api/confirm` to validate ownership before getting a response.
3. **EventBridge** fires on S3 `Object Created` under the `uploads/` prefix → starts the Step Functions state machine.
4. **State machine** (`infrastructure/statemachine/tax_pipeline.asl.json`):
   - `ExtractWithTextract` → `CheckConfidence` (Choice) → optionally `ClaudeFallback` → `ValidateDocument` → `StoreInDynamoDB` → `GeneratePDF` → `PipelineSucceeded`
   - Error states (`TextractFailed`, `ValidationFailed`, `StorageFailed`) call the error handler Lambda then terminate with `PipelineFailed`.
   - `ClaudeFallback` and `GeneratePDF` failures are non-fatal; the pipeline continues.
5. **Review UI**: user navigates to `/documents`, selects a doc, edits fields in `ReviewShell`, saves via `PUT /api/documents/[docId]`, downloads PDF via `POST /api/documents/[docId]/pdf` (streamed from S3, `Content-Disposition: attachment`).

### Authentication (Cognito PKCE)

All routes except `/login`, `/api/auth/*`, and static assets are gated by `middleware.ts` (Node.js runtime, not Edge).

- **Login flow**: `/api/auth/login` generates PKCE verifier + state → stores in `httpOnly` cookies → redirects to Cognito Hosted UI → callback at `/api/auth/callback` exchanges code for tokens → sets `tax_access_token`, `tax_id_token`, `tax_refresh_token` cookies.
- **Token validation**: `lib/auth.ts` uses `jose` (`createRemoteJWKSet` with RS256). Cognito access tokens use the `client_id` claim, not `aud` — **do not pass `audience` to `jwtVerify`**.
- **Header injection prevention**: middleware calls `upstream.delete('x-user-id')` before setting the JWT-derived value. Every API route reads identity exclusively from the `x-user-id` header.
- **Token refresh**: `authFetch()` in `FileUpload.tsx` transparently calls `POST /api/auth/refresh` on 401 `TOKEN_EXPIRED` and retries once.

### DynamoDB Table Design

Single table shared by document records and audit logs.

| Item type | PK (`user_id`) | SK (`doc_id`) |
|---|---|---|
| Tax document | Cognito `sub` UUID | `{type}#{year}#{uuid4}` e.g. `W2#2024#abc` |
| Audit log | Cognito `sub` UUID | `AUDIT#{iso_timestamp}#{uuid4}` |

- **PII fields** (SSN, EIN, addresses) are encrypted with AWS Encryption SDK v3 into the `encrypted_pii` Binary attribute. Encryption context binds `(user_id, doc_id, document_type, tax_year)`.
- Non-PII fields go into `schema_data` (DynamoDB Map).
- Per-field Textract confidence scores are stored in `field_metadata` (Map, `value` key stripped).
- TTL attribute: `expires_at` (Unix epoch). Default: 365 days.
- `update_document()` does a **read-modify-write**: fetches the existing item (decrypts PII), merges corrections, re-encrypts, and overwrites with `ConditionExpression="attribute_exists(user_id)"`.

### Lambda Package Layout

All Lambdas share the `lambda/` directory as the Python path root. Cross-Lambda imports work because CDK bundles the entire `lambda/` tree:

```
lambda/
├── tax_storage/      # shared PII encryption + DynamoDB repository
├── tax_models/       # shared Pydantic v2 models (W2, 1099-*)
├── textract_only/    # Textract extraction (used by pipeline)
├── claude_fallback_fn/
├── validator/
├── dynamodb_store/   # thin wrapper over tax_storage.TaxDocumentRepository
├── document_api/     # multi-action CRUD Lambda called from Next.js
├── pdf_generator/    # fpdf2-based PDF; uploads to pdfs/{user_id}/{doc_id}.pdf
└── error_handler/    # structured logging + SNS alerts
```

Handler paths use dot notation: `document_api.handler.handler`.

### CDK Stacks and Dependency Order

```
CognitoStack  (deploy first — outputs populate .env.local)
     ↓
W2TextractStack  (S3, KMS, DynamoDB, base IAM)
     ↓
TaxPipelineStack  (Step Functions, EventBridge, all pipeline Lambdas)
                  uses Fn.ImportValue to reference W2TextractStack exports
```

Lambda ARNs are injected into the ASL at deploy time via CDK `DefinitionSubstitutions` — the `${XxxFunctionArn}` tokens in the ASL JSON are not Bash variables, they are CDK substitution placeholders.

### S3 Key Conventions

- Uploaded documents: `uploads/{userId}/{YYYY-MM-DD}/{uuid}.{ext}`
- Generated PDFs: `pdfs/{userId}/{docId}.pdf`
- Presigned POST includes a `starts-with` condition enforcing the `uploads/{userId}/` prefix.

### Next.js API → Lambda Bridge

`lib/lambda.ts` exports `invokeFunction<T>(functionName, payload)`. Function names come from env vars:
- `DOCUMENT_API_FUNCTION_NAME`
- `PDF_GENERATOR_FUNCTION_NAME`

### URL-Encoding `doc_id` in Routes

`doc_id` values contain `#` (e.g. `W2#2024#uuid`). Always use `encodeURIComponent(doc.doc_id)` when building `href` strings. Next.js App Router decodes `params.docId` automatically on the server side.

## Environment Variables

Copy `.env.local.example` to `.env.local`. CDK stack outputs provide all values. Retrieve the Cognito client secret post-deploy:
```bash
aws cognito-idp describe-user-pool-client \
  --user-pool-id <UserPoolId> \
  --client-id <ClientId> \
  --query UserPoolClient.ClientSecret --output text
```

## Key Constraints

- **Textract QUERIES adapter hard limit is 30 queries**. The W-2 query list is asserted at import time (`assert len(_W2_QUERIES) <= 30`).
- **Cognito access tokens** do not have an `aud` claim — only `client_id`. Validating `aud` will always fail.
- **DynamoDB rejects Python `float`**. Use `_to_decimal()` / `_from_decimal()` helpers in `lambda/tax_storage/repository.py` for all numeric values.
- **`AUDIT#` SK prefix** is reserved for audit log entries. `get_all_docs_by_user()` filters these out automatically via a `FilterExpression` when listing documents.
- **Self-signup is disabled** in Cognito. Users must be created via `aws cognito-idp admin-create-user`.
