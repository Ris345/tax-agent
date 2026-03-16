'use client';

/**
 * ReviewShell — Client Component owning the editable form state.
 *
 * Props come from the ReviewPage Server Component (already has the document).
 * This component handles:
 *   - Local state for field values and dirty tracking
 *   - Save (PUT /api/documents/[docId]) and Discard actions
 *   - PDF download (POST /api/documents/[docId]/pdf → stream → browser save)
 */

import { useState, useCallback } from 'react';
import { FieldGroup } from './FieldGroup';
import type { SectionDef } from '@/lib/fieldDefs';

export interface FieldMeta {
  confidence:        number;
  flagged_for_review: boolean;
  source:            string;
}

interface ReviewShellProps {
  docId:         string;
  documentType:  string;
  taxYear:       number;
  document:      Record<string, unknown>;
  fieldMetadata: Record<string, FieldMeta>;
  sections:      SectionDef[];
}

type Status = 'idle' | 'saving' | 'saved' | 'generating_pdf' | 'error';

function allFieldValues(
  sections: SectionDef[],
  document: Record<string, unknown>,
): Record<string, string> {
  const values: Record<string, string> = {};
  for (const section of sections) {
    for (const field of section.fields) {
      const raw = document[field.key];
      values[field.key] = raw !== null && raw !== undefined ? String(raw) : '';
    }
  }
  return values;
}

export function ReviewShell({
  docId,
  documentType,
  taxYear,
  document: initialDoc,
  fieldMetadata,
  sections,
}: ReviewShellProps) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    allFieldValues(sections, initialDoc),
  );
  const [dirty,    setDirty]   = useState<Set<string>>(new Set());
  const [status,   setStatus]  = useState<Status>('idle');
  const [errorMsg, setErrorMsg]= useState('');

  const handleChange = useCallback((key: string, value: string) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    setDirty((prev) => new Set(prev).add(key));
  }, []);

  const handleDiscard = useCallback(() => {
    setValues(allFieldValues(sections, initialDoc));
    setDirty(new Set());
    setStatus('idle');
    setErrorMsg('');
  }, [initialDoc, sections]);

  const handleSave = useCallback(async () => {
    if (dirty.size === 0) return;
    setStatus('saving');
    setErrorMsg('');

    const corrections: Record<string, string> = {};
    for (const key of dirty) {
      corrections[key] = values[key];
    }

    try {
      const res = await fetch(`/api/documents/${encodeURIComponent(docId)}`, {
        method:  'PUT',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ corrections }),
      });

      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }

      setDirty(new Set());
      setStatus('saved');
      setTimeout(() => setStatus('idle'), 2500);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Save failed');
      setStatus('error');
    }
  }, [dirty, values, docId]);

  const handleDownloadPdf = useCallback(async () => {
    setStatus('generating_pdf');
    setErrorMsg('');

    try {
      const res = await fetch(`/api/documents/${encodeURIComponent(docId)}/pdf`, {
        method: 'POST',
      });

      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }

      const blob     = await res.blob();
      const url      = URL.createObjectURL(blob);
      const anchor   = document.createElement('a');
      anchor.href    = url;
      anchor.download = `${documentType}-${taxYear}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);

      setStatus('idle');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'PDF generation failed');
      setStatus('error');
    }
  }, [docId, documentType, taxYear]);

  const flaggedCount = Object.values(fieldMetadata).filter((m) => m.flagged_for_review).length;
  const isBusy       = status === 'saving' || status === 'generating_pdf';

  return (
    <main className="max-w-4xl mx-auto px-4 py-8">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            {documentType} — Tax Year {taxYear}
          </h1>
          {flaggedCount > 0 && (
            <p className="mt-1 text-sm text-amber-600">
              ⚠ {flaggedCount} field{flaggedCount !== 1 ? 's' : ''} flagged for review — low confidence extraction
            </p>
          )}
        </div>

        {/* ── Action bar ─────────────────────────────────────────────────── */}
        <div className="flex items-center gap-2">
          {dirty.size > 0 && (
            <button
              type="button"
              onClick={handleDiscard}
              disabled={isBusy}
              className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 transition-colors"
            >
              Discard
            </button>
          )}

          <button
            type="button"
            onClick={handleSave}
            disabled={dirty.size === 0 || isBusy}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50 transition-colors"
          >
            {status === 'saving'
              ? 'Saving…'
              : status === 'saved'
              ? '✓ Saved'
              : dirty.size > 0
              ? `Save (${dirty.size})`
              : 'Save'}
          </button>

          <button
            type="button"
            onClick={handleDownloadPdf}
            disabled={isBusy}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50 transition-colors"
          >
            {status === 'generating_pdf' ? 'Generating…' : '↓ Download PDF'}
          </button>
        </div>
      </div>

      {/* ── Error banner ─────────────────────────────────────────────────── */}
      {status === 'error' && errorMsg && (
        <div className="mb-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {errorMsg}
          <button
            type="button"
            onClick={() => setStatus('idle')}
            className="ml-3 underline hover:no-underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* ── Field sections ───────────────────────────────────────────────── */}
      <div className="space-y-4">
        {sections.map((section) => (
          <FieldGroup
            key={section.id}
            section={section}
            values={values}
            dirty={dirty}
            fieldMetadata={fieldMetadata}
            onChange={handleChange}
          />
        ))}
      </div>
    </main>
  );
}
