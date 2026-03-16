/**
 * GET /api/documents
 *
 * List all tax documents for the authenticated user.
 * Optional query params: ?type=W2&year=2024
 *
 * Returns: { documents: DocumentSummary[] }
 */

import { NextRequest, NextResponse } from 'next/server';
import { invokeFunction } from '@/lib/lambda';

export const runtime = 'nodejs';

export async function GET(request: NextRequest) {
  const userId = request.headers.get('x-user-id');
  if (!userId) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const { searchParams } = request.nextUrl;
  const documentType = searchParams.get('type') ?? undefined;
  const taxYearRaw   = searchParams.get('year');
  const taxYear      = taxYearRaw ? Number(taxYearRaw) : undefined;

  try {
    const result = await invokeFunction<{ documents: unknown[] }>(
      process.env.DOCUMENT_API_FUNCTION_NAME!,
      {
        action:        'list',
        user_id:       userId,
        document_type: documentType,
        tax_year:      taxYear,
      },
    );
    return NextResponse.json(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Internal error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
