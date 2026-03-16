/**
 * POST /api/documents/[docId]/pdf
 *
 * Generate (or re-generate) the PDF for a stored tax document and stream it
 * back to the browser as an attachment download.
 *
 * Flow
 * ----
 * 1. Fetch the document from DynamoDB (via DocumentApiFunction Lambda).
 * 2. Invoke PDFGeneratorFunction with the full document — it builds the PDF,
 *    uploads it to S3, and returns the S3 key.
 * 3. Retrieve the PDF bytes from S3 using GetObjectCommand.
 * 4. Write an immutable audit log entry in DynamoDB (non-blocking; failure is
 *    logged but does not abort the download).
 * 5. Return the PDF bytes as application/pdf with Content-Disposition: attachment.
 *
 * Security
 * --------
 * - `x-user-id` comes from middleware (JWT-validated) — never client-supplied.
 * - The document fetch uses (user_id, doc_id) as the DynamoDB key, so a user
 *   can only download their own documents.
 * - The S3 GetObject is server-side only; the presigned URL from the PDF
 *   generator is intentionally discarded (we stream bytes instead).
 */

import { NextRequest } from 'next/server';
import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3';
import { invokeFunction } from '@/lib/lambda';

export const runtime = 'nodejs';

const _s3 = new S3Client({ region: process.env.AWS_REGION ?? 'us-east-1' });

export async function POST(
  request: NextRequest,
  { params }: { params: { docId: string } },
) {
  const userId = request.headers.get('x-user-id');
  if (!userId) {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const docId = params.docId;

  // ── 1. Fetch document ──────────────────────────────────────────────────────
  let document: Record<string, unknown>;
  try {
    const getResult = await invokeFunction<{
      document: Record<string, unknown> | null;
    }>(process.env.DOCUMENT_API_FUNCTION_NAME!, {
      action:  'get',
      user_id: userId,
      doc_id:  docId,
    });

    if (!getResult.document) {
      return new Response(JSON.stringify({ error: 'Document not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    document = getResult.document;
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Lambda error';
    return new Response(JSON.stringify({ error: msg }), {
      status: 502,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // ── 2. Generate PDF ────────────────────────────────────────────────────────
  let pdfKey: string;
  try {
    const pdfResult = await invokeFunction<{
      pdf_key:      string;
      presigned_url: string;
      expires_in:   number;
    }>(process.env.PDF_GENERATOR_FUNCTION_NAME!, {
      document: document,
      doc_id:   docId,
      user_id:  userId,
      bucket:   process.env.S3_BUCKET_NAME!,
    });
    pdfKey = pdfResult.pdf_key;
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'PDF generation failed';
    return new Response(JSON.stringify({ error: msg }), {
      status: 502,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // ── 3. Retrieve PDF bytes from S3 ──────────────────────────────────────────
  let pdfBytes: Uint8Array;
  try {
    const s3Resp = await _s3.send(
      new GetObjectCommand({
        Bucket: process.env.S3_BUCKET_NAME!,
        Key:    pdfKey,
      }),
    );

    if (!s3Resp.Body) {
      return new Response(JSON.stringify({ error: 'PDF not found in S3' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    pdfBytes = await s3Resp.Body.transformToByteArray();
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'S3 fetch failed';
    return new Response(JSON.stringify({ error: msg }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // ── 4. Audit log (non-blocking — does not gate the download) ───────────────
  invokeFunction(process.env.DOCUMENT_API_FUNCTION_NAME!, {
    action:       'audit_log',
    user_id:      userId,
    doc_id:       docId,
    audit_action: 'pdf_download',
    ip_address:   request.headers.get('x-forwarded-for') ?? undefined,
    user_agent:   request.headers.get('user-agent') ?? undefined,
  }).catch((err: unknown) => {
    console.error(
      '[pdf/route] audit log failed (non-fatal):',
      err instanceof Error ? err.message : err,
    );
  });

  // ── 5. Stream PDF back ─────────────────────────────────────────────────────
  const parts    = docId.split('#');
  const docType  = parts[0] ?? 'tax';
  const taxYear  = parts[1] ?? 'document';
  const filename = `${docType}-${taxYear}.pdf`;

  return new Response(pdfBytes, {
    status: 200,
    headers: {
      'Content-Type':        'application/pdf',
      'Content-Disposition': `attachment; filename="${filename}"`,
      'Content-Length':      String(pdfBytes.byteLength),
      'Cache-Control':       'no-store, no-cache',
    },
  });
}
