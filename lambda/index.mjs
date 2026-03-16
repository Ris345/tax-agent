/**
 * Lambda triggered by S3 PutObject events.
 *
 * Responsibilities demonstrated here:
 *  1. Log uploaded object metadata (key, size, SSE type, KMS key ID).
 *  2. Validate that SSE-KMS is actually applied (defence-in-depth).
 *  3. Emit a structured CloudWatch log entry for downstream monitoring.
 *
 * Extend this handler to: run antivirus, extract text from PDFs,
 * write metadata to DynamoDB, send SNS/SQS notifications, etc.
 */

/**
 * @param {import('aws-lambda').S3Event} event
 */
export async function handler(event) {
  for (const record of event.Records) {
    const { s3 } = record;
    const bucket = s3.bucket.name;
    const key = decodeURIComponent(s3.object.key.replace(/\+/g, ' '));
    const sizeBytes = s3.object.size;
    const eTag = s3.object.eTag;

    // s3:ObjectCreated:Put events include the SSE fields in the record
    const sseAlgorithm = record.s3.object['x-amz-server-side-encryption'] ?? 'NONE';
    const sseKmsKeyId = record.s3.object['x-amz-server-side-encryption-aws-kms-key-id'] ?? null;

    const logEntry = {
      event: 'S3_OBJECT_CREATED',
      timestamp: new Date().toISOString(),
      bucket,
      key,
      sizeBytes,
      eTag,
      sseAlgorithm,
      sseKmsKeyId,
    };

    // Defence-in-depth: alert if an object somehow arrived without SSE-KMS
    if (sseAlgorithm !== 'aws:kms') {
      console.error(JSON.stringify({ ...logEntry, alert: 'MISSING_SSE_KMS' }));
      // In production: send to SNS / PagerDuty / Security Hub here
    } else {
      console.log(JSON.stringify(logEntry));
    }
  }
}
