/**
 * /documents/[docId]/review — Review page for a single tax document.
 *
 * Server Component: fetches the document from DynamoDB and passes the data
 * to the ReviewShell Client Component which owns the editable form state.
 */

import type { Metadata } from 'next';
import { headers } from 'next/headers';
import { notFound } from 'next/navigation';
import { invokeFunction } from '@/lib/lambda';
import { FIELD_SECTIONS } from '@/lib/fieldDefs';
import { ReviewShell } from '@/components/review/ReviewShell';

export const metadata: Metadata = { title: 'Review Document — Tax Agent' };

interface ReviewPageProps {
  params: { docId: string };
}

interface FieldMeta {
  confidence:        number;
  flagged_for_review: boolean;
  source:            string;
}

export default async function ReviewPage({ params }: ReviewPageProps) {
  const hdrs   = headers();
  const userId = hdrs.get('x-user-id');
  if (!userId) return notFound();

  // Next.js decodes the path segment automatically, so params.docId is the
  // raw doc_id string (e.g. "W2#2024#<uuid>").
  const docId = params.docId;

  let document: Record<string, unknown>;
  try {
    const result = await invokeFunction<{
      document: Record<string, unknown> | null;
    }>(process.env.DOCUMENT_API_FUNCTION_NAME!, {
      action:  'get',
      user_id: userId,
      doc_id:  docId,
    });

    if (!result.document) return notFound();
    document = result.document;
  } catch {
    return notFound();
  }

  const documentType = String(document.document_type ?? '');
  const taxYear      = Number(document.tax_year ?? 0);
  const sections     = FIELD_SECTIONS[documentType] ?? [];
  const fieldMetadata = (document.field_metadata ?? {}) as Record<string, FieldMeta>;

  return (
    <ReviewShell
      docId={docId}
      documentType={documentType}
      taxYear={taxYear}
      document={document}
      fieldMetadata={fieldMetadata}
      sections={sections}
    />
  );
}
