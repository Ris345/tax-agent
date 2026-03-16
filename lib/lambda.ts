/**
 * Server-side helper for invoking AWS Lambda functions from Next.js API routes.
 *
 * The Lambda client is module-level so the underlying HTTP connection pool is
 * reused across requests within the same Node.js process (Next.js server).
 */

import {
  LambdaClient,
  InvokeCommand,
  InvokeCommandOutput,
} from '@aws-sdk/client-lambda';

const _client = new LambdaClient({
  region: process.env.AWS_REGION ?? 'us-east-1',
});

/**
 * Invoke a Lambda function and return the parsed JSON response payload.
 *
 * Throws if:
 *   - The Lambda service returns a non-2xx status code
 *   - The Lambda function itself throws (FunctionError is set)
 *
 * @param functionName  Function name or full ARN.
 * @param payload       JSON-serialisable input payload.
 */
export async function invokeFunction<T = unknown>(
  functionName: string,
  payload: unknown,
): Promise<T> {
  const command = new InvokeCommand({
    FunctionName: functionName,
    InvocationType: 'RequestResponse',
    Payload: Buffer.from(JSON.stringify(payload)),
  });

  const response: InvokeCommandOutput = await _client.send(command);

  const raw = response.Payload ? Buffer.from(response.Payload).toString('utf-8') : '{}';

  if (response.FunctionError) {
    let message = response.FunctionError;
    try {
      const parsed = JSON.parse(raw) as { errorMessage?: string };
      message = parsed.errorMessage ?? message;
    } catch {
      // ignore JSON parse failure — use raw FunctionError string
    }
    throw new Error(`Lambda invocation error (${functionName}): ${message}`);
  }

  return JSON.parse(raw) as T;
}
