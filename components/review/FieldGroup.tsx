'use client';

/**
 * FieldGroup — collapsible section of related tax fields.
 *
 * Shows the section title and a "flagged" badge if any fields in the section
 * have low-confidence extractions.  Clicking the header toggles the content.
 */

import { useState } from 'react';
import { EditableField } from './EditableField';
import type { SectionDef } from '@/lib/fieldDefs';
import type { FieldMeta } from './ReviewShell';

interface FieldGroupProps {
  section:       SectionDef;
  values:        Record<string, string>;
  dirty:         Set<string>;
  fieldMetadata: Record<string, FieldMeta>;
  onChange:      (key: string, value: string) => void;
}

export function FieldGroup({
  section,
  values,
  dirty,
  fieldMetadata,
  onChange,
}: FieldGroupProps) {
  const [open, setOpen] = useState(true);

  const flaggedCount = section.fields.filter(
    (f) => fieldMetadata[f.key]?.flagged_for_review,
  ).length;

  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden shadow-sm">
      {/* Section header */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-5 py-4 text-left hover:bg-gray-50 transition-colors"
        aria-expanded={open}
      >
        <div className="flex items-center gap-3">
          <span className="font-semibold text-gray-800 text-sm">{section.title}</span>
          {flaggedCount > 0 && (
            <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800">
              {flaggedCount} flagged
            </span>
          )}
        </div>
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className={`h-4 w-4 text-gray-400 transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Field rows */}
      {open && (
        <div className="border-t border-gray-100 divide-y divide-gray-100">
          {section.fields.map((field) => (
            <EditableField
              key={field.key}
              field={field}
              value={values[field.key] ?? ''}
              isDirty={dirty.has(field.key)}
              meta={fieldMetadata[field.key]}
              onChange={(v) => onChange(field.key, v)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
