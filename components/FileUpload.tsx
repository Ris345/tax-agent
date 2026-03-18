'use client';

import { useRef, useState, DragEvent, ChangeEvent } from 'react';

// ── Auth-aware fetch ─────────────────────────────────────────────────────────
// Transparently attempts one token refresh before surfacing a 401 to the UI.
// Uses httpOnly cookies — no token handling in JS.

async function authFetch(input: string, init: RequestInit): Promise<Response> {
  const res = await fetch(input, { ...init, credentials: 'same-origin' });
  if (res.status !== 401) return res;

  const body = await res.clone().json().catch(() => ({})) as { code?: string };
  if (body.code !== 'TOKEN_EXPIRED') return res; // unrecoverable — surface as-is

  // Attempt silent token refresh.
  const refresh = await fetch('/api/auth/refresh', { method: 'POST', credentials: 'same-origin' });
  if (!refresh.ok) {
    // Refresh token also expired — redirect to login.
    window.location.href = '/login';
    return res; // never reached, silences TS
  }

  // Retry original request once with the new access token (now in cookie).
  return fetch(input, { ...init, credentials: 'same-origin' });
}


type UploadState =
  | { status: 'idle' }
  | { status: 'validating' }
  | { status: 'requesting_url' }
  | { status: 'uploading'; progress: number }
  | { status: 'confirming' }
  | { status: 'done'; key: string; presignedUrl: string; expiresIn: number }
  | { status: 'error'; message: string };

const ALLOWED_TYPES = [
  'application/pdf',
  'image/jpeg',
  'image/png',
  'image/gif',
  'image/webp',
  'image/tiff',
];
const MAX_BYTES = 10 * 1024 * 1024;
const ACCEPT = '.pdf,.jpg,.jpeg,.png,.gif,.webp,.tif,.tiff';

const DOC_TYPE_OPTIONS = [
  { value: 'W2',       label: 'W-2 — Wage and Tax Statement' },
  { value: '1099-NEC', label: '1099-NEC — Nonemployee Compensation' },
  { value: '1099-INT', label: '1099-INT — Interest Income' },
] as const;

type DocType = (typeof DOC_TYPE_OPTIONS)[number]['value'];

export default function FileUpload() {
  const [state, setState] = useState<UploadState>({ status: 'idle' });
  const [dragOver, setDragOver] = useState(false);
  const [documentType, setDocumentType] = useState<DocType | ''>('');
  const inputRef = useRef<HTMLInputElement>(null);

  function validate(file: File): string | null {
    if (!ALLOWED_TYPES.includes(file.type)) return 'Only PDF and image files are accepted.';
    if (file.size > MAX_BYTES) return 'File exceeds 10 MB limit.';
    if (file.size === 0) return 'File is empty.';
    return null;
  }

  async function handleFile(file: File) {
    if (!documentType) {
      setState({ status: 'error', message: 'Please select a document type before uploading.' });
      return;
    }

    const validationError = validate(file);
    if (validationError) {
      setState({ status: 'error', message: validationError });
      return;
    }

    // 1 — Request presigned POST from our API
    setState({ status: 'requesting_url' });
    let presignData: { url: string; fields: Record<string, string>; key: string };
    try {
      const res = await authFetch('/api/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fileName: file.name, contentType: file.type, fileSize: file.size, documentType }),
      });
      if (!res.ok) {
        const { error } = await res.json();
        throw new Error(error ?? `API error ${res.status}`);
      }
      presignData = await res.json();
    } catch (err: unknown) {
      setState({ status: 'error', message: (err as Error).message });
      return;
    }

    // 2 — Upload directly to S3 using the presigned POST fields
    setState({ status: 'uploading', progress: 0 });
    try {
      await uploadToS3(presignData.url, presignData.fields, file, (progress) => {
        setState({ status: 'uploading', progress });
      });
    } catch (err: unknown) {
      setState({ status: 'error', message: (err as Error).message });
      return;
    }

    // 3 — Confirm with our API and get a presigned GET URL
    setState({ status: 'confirming' });
    try {
      const res = await authFetch('/api/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: presignData.key }),
      });
      if (!res.ok) {
        const { error } = await res.json();
        throw new Error(error ?? `Confirm API error ${res.status}`);
      }
      const { key, presignedUrl, expiresIn } = await res.json();
      setState({ status: 'done', key, presignedUrl, expiresIn });
    } catch (err: unknown) {
      setState({ status: 'error', message: (err as Error).message });
    }
  }

  function onInputChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    // Reset so same file can be re-selected after an error
    e.target.value = '';
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }

  const busy =
    state.status === 'validating' ||
    state.status === 'requesting_url' ||
    state.status === 'uploading' ||
    state.status === 'confirming';

  return (
    <div className="w-full max-w-lg mx-auto space-y-4">
      {/* Document type selector */}
      <div className="space-y-1">
        <label htmlFor="doc-type" className="block text-sm font-medium text-gray-700">
          Document type
        </label>
        <select
          id="doc-type"
          value={documentType}
          onChange={(e) => setDocumentType(e.target.value as DocType)}
          disabled={busy}
          className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-60"
        >
          <option value="">Select a form type...</option>
          {DOC_TYPE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      {/* Drop zone */}
      <div
        onClick={() => !busy && documentType && inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={[
          'relative flex flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed p-10 transition-colors select-none',
          dragOver ? 'border-blue-500 bg-blue-50' : 'border-gray-300 bg-gray-50 hover:border-blue-400',
          busy || !documentType ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer',
        ].join(' ')}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          onChange={onInputChange}
          disabled={busy}
        />

        <UploadIcon />

        <p className="text-sm font-medium text-gray-700">
          {dragOver ? 'Drop to upload' : 'Drag & drop or click to select'}
        </p>
        <p className="text-xs text-gray-400">PDF, JPEG, PNG, GIF, WEBP, TIFF — max 10 MB</p>
        <p className="text-xs text-gray-400">Stored with SSE-KMS encryption in a private S3 bucket</p>
      </div>

      {/* Status panel */}
      <StatusPanel state={state} onReset={() => setState({ status: 'idle' })} />
    </div>
  );
}

// ─── Status panel ────────────────────────────────────────────────────────────

function StatusPanel({
  state,
  onReset,
}: {
  state: UploadState;
  onReset: () => void;
}) {
  if (state.status === 'idle') return null;

  if (state.status === 'requesting_url') {
    return <InfoBanner>Requesting secure upload URL...</InfoBanner>;
  }

  if (state.status === 'uploading') {
    return (
      <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 space-y-2">
        <p className="text-sm font-medium text-blue-700">Uploading to S3...</p>
        <div className="w-full h-2 rounded-full bg-blue-200 overflow-hidden">
          <div
            className="h-2 rounded-full bg-blue-500 transition-all"
            style={{ width: `${state.progress}%` }}
          />
        </div>
        <p className="text-xs text-blue-500 text-right">{state.progress}%</p>
      </div>
    );
  }

  if (state.status === 'confirming') {
    return <InfoBanner>Confirming upload and generating verification URL...</InfoBanner>;
  }

  if (state.status === 'error') {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 space-y-2">
        <p className="text-sm font-medium text-red-700">Upload failed</p>
        <p className="text-xs text-red-500">{state.message}</p>
        <button
          onClick={onReset}
          className="text-xs text-red-600 underline hover:no-underline"
        >
          Try again
        </button>
      </div>
    );
  }

  if (state.status === 'done') {
    const expiresAt = new Date(Date.now() + state.expiresIn * 1000).toLocaleTimeString();
    return (
      <div className="rounded-xl border border-green-200 bg-green-50 p-4 space-y-3">
        <p className="text-sm font-semibold text-green-700">Upload successful</p>

        <div className="space-y-1">
          <Label>S3 Key</Label>
          <Code>{state.key}</Code>
        </div>

        <div className="space-y-1">
          <Label>Presigned confirmation URL (expires ~{expiresAt})</Label>
          <a
            href={state.presignedUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="block break-all text-xs text-blue-600 hover:underline"
          >
            {state.presignedUrl}
          </a>
        </div>

        <button
          onClick={onReset}
          className="text-xs text-green-700 underline hover:no-underline"
        >
          Upload another file
        </button>
      </div>
    );
  }

  return null;
}

function InfoBanner({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 text-sm text-gray-600 animate-pulse">
      {children}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <p className="text-xs font-medium text-gray-500">{children}</p>;
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded bg-white border border-gray-200 px-2 py-1 font-mono text-xs text-gray-700 break-all">
      {children}
    </p>
  );
}

// ─── S3 presigned POST upload (XMLHttpRequest for progress) ──────────────────

function uploadToS3(
  url: string,
  fields: Record<string, string>,
  file: File,
  onProgress: (pct: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    // S3 requires all presigned fields to appear BEFORE the file field
    for (const [k, v] of Object.entries(fields)) form.append(k, v);
    form.append('file', file);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    });

    xhr.addEventListener('load', () => {
      // S3 presigned POST returns 204 on success
      if (xhr.status === 204 || xhr.status === 200) {
        onProgress(100);
        resolve();
      } else {
        reject(new Error(`S3 upload failed: HTTP ${xhr.status}`));
      }
    });

    xhr.addEventListener('error', () => reject(new Error('Network error during S3 upload.')));
    xhr.addEventListener('abort', () => reject(new Error('Upload cancelled.')));

    xhr.send(form);
  });
}

// ─── Icon ────────────────────────────────────────────────────────────────────

function UploadIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      className="h-10 w-10 text-gray-400"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.5}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
      />
    </svg>
  );
}
