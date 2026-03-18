import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3';
import { createPresignedPost, PresignedPost } from '@aws-sdk/s3-presigned-post';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { randomUUID } from 'crypto';

export const ALLOWED_MIME_TYPES = [
  'application/pdf',
  'image/jpeg',
  'image/png',
  'image/gif',
  'image/webp',
  'image/tiff',
] as const;

export type AllowedMimeType = (typeof ALLOWED_MIME_TYPES)[number];

export const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024; // 10 MB

export const SUPPORTED_DOC_TYPES = ['W2', '1099-NEC', '1099-INT'] as const;
export type SupportedDocType = (typeof SUPPORTED_DOC_TYPES)[number];

const s3 = new S3Client({
  region: process.env.AWS_REGION ?? 'us-east-1',
  // In production on AWS (EC2/ECS/Lambda), credentials are picked up
  // automatically from the instance metadata / task role.
  // For local dev, set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY in .env.local
});

/**
 * Build an S3 key scoped to the authenticated user.
 *
 * Format: uploads/{userId}/{YYYY-MM-DD}/{docType}/{uuid}.{ext}
 *
 * The userId segment means:
 *  - The presigned POST condition `starts-with($key, "uploads/{userId}/")`
 *    prevents one user from uploading into another user's prefix.
 *  - The confirm route can validate key ownership without a DB lookup.
 *  - The Step Functions pipeline extracts userId from key.split('/')[1]
 *    and document_type from key.split('/')[3].
 */
function buildKey(userId: string, originalName: string, documentType: SupportedDocType): string {
  const ext  = originalName.split('.').pop() ?? 'bin';
  const date = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
  return `uploads/${userId}/${date}/${documentType}/${randomUUID()}.${ext}`;
}

/**
 * Generate a presigned POST policy that:
 *  - Scopes the upload key to the authenticated user's prefix
 *  - Restricts content-type to the requested MIME type
 *  - Caps upload size at MAX_FILE_SIZE_BYTES
 *  - Enforces SSE-KMS encryption with the configured KMS key
 *
 * The client must include ALL returned `fields` in the multipart form POST to S3.
 */
export async function generatePresignedPost(
  userId: string,
  fileName: string,
  contentType: AllowedMimeType,
  documentType: SupportedDocType,
): Promise<{ url: string; fields: Record<string, string>; key: string }> {
  const bucket = process.env.S3_BUCKET_NAME;
  const kmsKeyId = process.env.KMS_KEY_ID;

  if (!bucket) throw new Error('S3_BUCKET_NAME environment variable is not set');
  if (!kmsKeyId) throw new Error('KMS_KEY_ID environment variable is not set');

  const key = buildKey(userId, fileName, documentType);

  const { url, fields }: PresignedPost = await createPresignedPost(s3, {
    Bucket: bucket,
    Key: key,
    Conditions: [
      // Enforce file-size range (1 byte – 10 MB)
      ['content-length-range', 1, MAX_FILE_SIZE_BYTES],
      // Scope upload to this user's prefix — S3 rejects keys outside it.
      // starts-with is used because the full key (with UUID) is unknown at
      // policy-generation time; the key field in Fields pins it exactly.
      ['starts-with', '$key', `uploads/${userId}/`],
      // Enforce the exact content-type the client declared
      ['eq', '$Content-Type', contentType],
      // Enforce SSE-KMS — upload is rejected by S3 if headers are absent
      ['eq', '$x-amz-server-side-encryption', 'aws:kms'],
      ['eq', '$x-amz-server-side-encryption-aws-kms-key-id', kmsKeyId],
    ],
    Fields: {
      'Content-Type': contentType,
      'x-amz-server-side-encryption': 'aws:kms',
      'x-amz-server-side-encryption-aws-kms-key-id': kmsKeyId,
    },
    Expires: 300, // presigned POST expires in 5 minutes
  });

  return { url, fields, key };
}

/**
 * Generate a short-lived presigned GET URL so the caller can verify the
 * object exists in S3 without making the bucket public.
 */
export async function generatePresignedGet(key: string): Promise<string> {
  const bucket = process.env.S3_BUCKET_NAME;
  if (!bucket) throw new Error('S3_BUCKET_NAME environment variable is not set');

  const expiresIn = Number(process.env.PRESIGNED_URL_EXPIRY_SECONDS ?? 3600);

  const command = new GetObjectCommand({ Bucket: bucket, Key: key });
  return getSignedUrl(s3, command, { expiresIn });
}
