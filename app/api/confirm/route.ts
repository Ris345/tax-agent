import { NextRequest, NextResponse } from 'next/server';
import { generatePresignedGet } from '@/lib/s3';

export async function POST(req: NextRequest) {
  // ── Identity ────────────────────────────────────────────────────────────────
  const userId = req.headers.get('x-user-id');
  if (!userId) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  try {
    const body = await req.json();
    const { key } = body as { key?: string };

    if (!key || typeof key !== 'string') {
      return NextResponse.json({ error: 'Invalid or missing S3 key.' }, { status: 400 });
    }

    // ── Ownership check ─────────────────────────────────────────────────────
    // Keys are generated as uploads/{userId}/{date}/{uuid}.{ext}.
    // A user must only be able to obtain a presigned GET URL for their own
    // objects. This check prevents horizontal privilege escalation where a
    // user supplies another user's key to access their documents.
    const expectedPrefix = `uploads/${userId}/`;
    if (!key.startsWith(expectedPrefix)) {
      return NextResponse.json(
        { error: 'Key does not belong to the authenticated user.' },
        { status: 403 },
      );
    }

    const presignedUrl = await generatePresignedGet(key);

    return NextResponse.json({
      key,
      presignedUrl,
      expiresIn: Number(process.env.PRESIGNED_URL_EXPIRY_SECONDS ?? 3600),
      message: 'Upload confirmed. Use presignedUrl to verify the object in S3.',
    });
  } catch (err) {
    console.error('[/api/confirm]', err);
    return NextResponse.json({ error: 'Failed to confirm upload.' }, { status: 500 });
  }
}
