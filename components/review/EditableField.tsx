'use client';

/**
 * EditableField — single extracted tax field with:
 *   - Confidence badge (green ≥85%, amber 70-84%, red <70%)
 *   - PII masking: SSN and EIN are masked until the user clicks "Show"
 *   - Dirty indicator (indigo dot) when the value has been modified
 *   - Amber highlight border when the field was flagged for review
 */

import { useState } from 'react';
import type { FieldDef } from '@/lib/fieldDefs';
import type { FieldMeta } from './ReviewShell';

interface EditableFieldProps {
  field:    FieldDef;
  value:    string;
  isDirty:  boolean;
  meta?:    FieldMeta;
  onChange: (value: string) => void;
}

// ── Confidence badge ──────────────────────────────────────────────────────────

function ConfidenceBadge({ meta }: { meta?: FieldMeta }) {
  if (!meta) return null;

  const { confidence } = meta;
  let colorClass: string;

  if (confidence >= 85) {
    colorClass = 'bg-green-100 text-green-700';
  } else if (confidence >= 70) {
    colorClass = 'bg-amber-100 text-amber-700';
  } else {
    colorClass = 'bg-red-100 text-red-700';
  }

  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium tabular-nums ${colorClass}`}
      title={`Extraction confidence: ${confidence.toFixed(1)}%`}
    >
      {confidence > 0 ? `${confidence.toFixed(0)}%` : 'N/A'}
    </span>
  );
}

// ── PII masking helpers ───────────────────────────────────────────────────────

function maskValue(value: string, type: string): string {
  if (!value) return '';
  const digits = value.replace(/\D/g, '');
  if (type === 'ssn') {
    return digits.length >= 4 ? `•••-••-${digits.slice(-4)}` : '••••';
  }
  if (type === 'ein') {
    return digits.length >= 4 ? `••-•••${digits.slice(-4)}` : '••••';
  }
  // Generic sensitive field — show last 4 chars
  return value.length > 4 ? `${'•'.repeat(Math.min(value.length - 4, 8))}${value.slice(-4)}` : '••••';
}

// ── Main component ────────────────────────────────────────────────────────────

export function EditableField({ field, value, isDirty, meta, onChange }: EditableFieldProps) {
  const [revealed, setRevealed] = useState(false);

  const isSensitive  = Boolean(field.sensitive);
  const showMasked   = isSensitive && !revealed;
  const isFlagged    = Boolean(meta?.flagged_for_review);

  const inputBaseClass = [
    'flex-1 rounded-md border px-3 py-1.5 text-sm transition-colors',
    'focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500',
    isFlagged  ? 'border-amber-300 bg-amber-50' : 'border-gray-300 bg-white',
    isDirty    ? 'ring-1 ring-indigo-200' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className="flex items-start gap-4 px-5 py-3">
      {/* ── Label + meta column ────────────────────────────────────────── */}
      <div className="w-52 shrink-0 pt-1.5">
        <div className="flex items-center gap-1.5">
          {isDirty && (
            <span
              className="inline-block h-2 w-2 rounded-full bg-indigo-500 shrink-0"
              title="Modified"
              aria-label="Field modified"
            />
          )}
          <label className="text-sm font-medium text-gray-700 leading-snug">
            {field.label}
            {field.required && <span className="ml-0.5 text-red-500" aria-hidden>*</span>}
          </label>
        </div>
        <div className="mt-1 flex items-center gap-1.5">
          <ConfidenceBadge meta={meta} />
          {meta && (
            <span className="text-xs text-gray-400">{meta.source}</span>
          )}
        </div>
      </div>

      {/* ── Input column ───────────────────────────────────────────────── */}
      <div className="flex-1 flex items-center gap-2 min-w-0">
        {field.type === 'boolean' ? (
          <select
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className={`w-28 ${inputBaseClass}`}
          >
            <option value="">—</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        ) : showMasked ? (
          /* Masked view: read-only placeholder + Show button */
          <div className="flex-1 flex items-center gap-2 min-w-0">
            <span className="flex-1 rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm text-gray-500 font-mono truncate">
              {maskValue(value, field.type) || <span className="text-gray-300">—</span>}
            </span>
            <button
              type="button"
              onClick={() => setRevealed(true)}
              className="shrink-0 text-xs text-indigo-600 hover:text-indigo-800 font-medium"
            >
              Show
            </button>
          </div>
        ) : (
          /* Editable view */
          <>
            <input
              type={field.type === 'currency' || field.type === 'number' ? 'number' : 'text'}
              value={value}
              onChange={(e) => onChange(e.target.value)}
              placeholder={field.placeholder ?? ''}
              step={field.type === 'currency' ? '0.01' : undefined}
              min={field.type === 'currency' || field.type === 'number' ? '0' : undefined}
              className={inputBaseClass}
            />
            {isSensitive && (
              <button
                type="button"
                onClick={() => setRevealed(false)}
                className="shrink-0 text-xs text-gray-400 hover:text-gray-600 font-medium"
              >
                Hide
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
