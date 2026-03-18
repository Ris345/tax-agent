import { NextRequest, NextResponse } from 'next/server';
import {
  ALLOWED_MIME_TYPES,
  AllowedMimeType,
  MAX_FILE_SIZE_BYTES,
  SUPPORTED_DOC_TYPES,
  SupportedDocType,
  generatePresignedPost,
} from '@/lib/s3';

export async function POST(req: NextRequest) {
  // ── Identity ────────────────────────────────────────────────────────────────
  // x-user-id is set exclusively by the Edge middleware after JWT verification.
  // Any client-supplied value is stripped by the middleware before this runs.
  const userId = req.headers.get('x-user-id');
  if (!userId) {
    // Middleware should have caught this; guard defensively.
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  try {
    const body = await req.json();
    const { fileName, contentType, fileSize, documentType } = body as {
      fileName?:     string;
      contentType?:  string;
      fileSize?:     number;
      documentType?: string;
    };

    // ── Validation ──────────────────────────────────────────────────────────
    if (!fileName || typeof fileName !== 'string') {
      return NextResponse.json({ error: 'fileName is required' }, { status: 400 });
    }

    if (!contentType || !(ALLOWED_MIME_TYPES as readonly string[]).includes(contentType)) {
      return NextResponse.json(
        {
          error: 'Unsupported file type. Allowed: PDF and images (JPEG, PNG, GIF, WEBP, TIFF).',
          allowed: ALLOWED_MIME_TYPES,
        },
        { status: 415 },
      );
    }

    if (typeof fileSize !== 'number' || fileSize <= 0 || fileSize > MAX_FILE_SIZE_BYTES) {
      return NextResponse.json(
        { error: `File size must be between 1 byte and ${MAX_FILE_SIZE_BYTES / 1024 / 1024} MB.` },
        { status: 413 },
      );
    }

    if (!documentType || !(SUPPORTED_DOC_TYPES as readonly string[]).includes(documentType)) {
      return NextResponse.json(
        {
          error: 'documentType is required. Supported values: ' + SUPPORTED_DOC_TYPES.join(', '),
          supported: SUPPORTED_DOC_TYPES,
        },
        { status: 400 },
      );
    }

    // ── Presigned POST scoped to this user ──────────────────────────────────
    // Key format: uploads/{userId}/{date}/{docType}/{uuid}.{ext}
    // The policy condition enforces starts-with on the user prefix,
    // preventing uploads to any other user's path.
    const { url, fields, key } = await generatePresignedPost(
      userId,
      fileName,
      contentType as AllowedMimeType,
      documentType as SupportedDocType,
    );

    return NextResponse.json({ url, fields, key });
  } catch (err) {
    console.error('[/api/upload]', err);
    return NextResponse.json({ error: 'Failed to generate upload URL.' }, { status: 500 });
  }
}
