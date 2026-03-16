/**
 * GET  /api/documents/[docId]  — fetch a single document (PII decrypted)
 * PUT  /api/documents/[docId]  — apply user corrections
 *
 * GET returns: { document: Record<string, unknown> }
 * PUT body:    { corrections: Record<string, unknown> }
 * PUT returns: { doc_id: string, updated: true }
 */

import { NextRequest, NextResponse } from 'next/server';
import { invokeFunction } from '@/lib/lambda';

export const runtime = 'nodejs';

interface RouteContext {
  params: { docId: string };
}

export async function GET(request: NextRequest, { params }: RouteContext) {
  const userId = request.headers.get('x-user-id');
  if (!userId) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  // Next.js App Router decodes dynamic path segments automatically.
  const docId = params.docId;

  try {
    const result = await invokeFunction<{ document: Record<string, unknown> | null }>(
      process.env.DOCUMENT_API_FUNCTION_NAME!,
      { action: 'get', user_id: userId, doc_id: docId },
    );

    if (!result.document) {
      return NextResponse.json({ error: 'Not found' }, { status: 404 });
    }
    return NextResponse.json(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Internal error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function PUT(request: NextRequest, { params }: RouteContext) {
  const userId = request.headers.get('x-user-id');
  if (!userId) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const docId = params.docId;

  let body: { corrections?: unknown };
  try {
    body = (await request.json()) as { corrections?: unknown };
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  if (!body.corrections || typeof body.corrections !== 'object') {
    return NextResponse.json(
      { error: '"corrections" object is required' },
      { status: 400 },
    );
  }

  try {
    const result = await invokeFunction(
      process.env.DOCUMENT_API_FUNCTION_NAME!,
      { action: 'update', user_id: userId, doc_id: docId, corrections: body.corrections },
    );
    return NextResponse.json(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Internal error';
    // Surface 404 if the Lambda reports the document was not found.
    const status = message.includes('not found') ? 404 : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
