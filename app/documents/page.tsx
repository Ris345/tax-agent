/**
 * /documents — Document list page.
 *
 * Server Component: fetches the user's tax documents via the Document API
 * Lambda and renders a list of clickable cards linking to each review page.
 */

import type { Metadata } from 'next';
import Link from 'next/link';
import { headers } from 'next/headers';
import { invokeFunction } from '@/lib/lambda';

export const metadata: Metadata = { title: 'Your Documents — Tax Agent' };

interface DocumentSummary {
  doc_id:        string;
  document_type: string;
  tax_year:      number;
  created_at:    string;
  updated_at?:   string;
  source:        string;
}

async function fetchDocuments(userId: string): Promise<DocumentSummary[]> {
  try {
    const result = await invokeFunction<{ documents: DocumentSummary[] }>(
      process.env.DOCUMENT_API_FUNCTION_NAME!,
      { action: 'list', user_id: userId },
    );
    return result.documents ?? [];
  } catch (err) {
    console.error('[documents/page] fetch failed:', err);
    return [];
  }
}

const SOURCE_LABELS: Record<string, string> = {
  textract:       'AI-extracted',
  claude:         'AI-extracted (Claude)',
  user_corrected: 'Reviewed',
};

export default async function DocumentsPage() {
  const hdrs   = headers();
  const userId = hdrs.get('x-user-id');

  if (!userId) {
    return (
      <main className="min-h-screen flex items-center justify-center">
        <p className="text-gray-500">Unauthorized</p>
      </main>
    );
  }

  const documents = await fetchDocuments(userId);

  return (
    <main className="max-w-4xl mx-auto px-4 py-8">
      {/* Page header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Your Tax Documents</h1>
        <Link
          href="/"
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 transition-colors"
        >
          Upload document
        </Link>
      </div>

      {documents.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-gray-300 py-16 text-center">
          <p className="text-gray-500 text-lg mb-3">No documents yet.</p>
          <Link
            href="/"
            className="text-indigo-600 hover:underline text-sm font-medium"
          >
            Upload your first tax document →
          </Link>
        </div>
      ) : (
        <div className="space-y-3">
          {documents.map((doc) => {
            const reviewHref = `/documents/${encodeURIComponent(doc.doc_id)}/review`;
            const label      = SOURCE_LABELS[doc.source] ?? doc.source;
            const date       = doc.updated_at ?? doc.created_at;

            return (
              <Link
                key={doc.doc_id}
                href={reviewHref}
                className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-5 py-4 shadow-sm hover:shadow-md hover:border-indigo-200 transition-all"
              >
                <div className="flex items-center gap-3">
                  <span className="inline-flex items-center rounded-md bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-700/10">
                    {doc.document_type}
                  </span>
                  <span className="text-sm font-medium text-gray-900">
                    Tax Year {doc.tax_year}
                  </span>
                </div>
                <div className="flex items-center gap-4">
                  <span className="text-xs text-gray-400">{label}</span>
                  <span className="text-xs text-gray-400">
                    {new Date(date).toLocaleDateString()}
                  </span>
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    className="h-4 w-4 text-gray-400"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </main>
  );
}
