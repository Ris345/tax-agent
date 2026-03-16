/**
 * /login — Login page.
 *
 * Clicking "Sign in" hits /api/auth/login which generates PKCE state,
 * stores it in httpOnly cookies, and redirects to the Cognito Hosted UI.
 *
 * Query params surfaced by the auth flow:
 *   ?error=... — shown as a banner when Cognito or our callback returns an error
 */

import type { Metadata } from 'next';

export const metadata: Metadata = { title: 'Sign in — Tax Agent' };

interface LoginPageProps {
  searchParams: { error?: string };
}

const ERROR_MESSAGES: Record<string, string> = {
  missing_params:        'Authorization response was incomplete. Please try again.',
  session_expired:       'Your sign-in session expired. Please try again.',
  state_mismatch:        'Security check failed. Please try again.',
  token_exchange_failed: 'Could not complete sign-in. Please try again.',
  access_denied:         'Access was denied. Contact your administrator.',
};

export default function LoginPage({ searchParams }: LoginPageProps) {
  const errorCode = searchParams.error;
  const errorMsg  = errorCode
    ? (ERROR_MESSAGES[errorCode] ?? 'An unexpected error occurred. Please try again.')
    : null;

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm space-y-6">
        {/* Logo / heading */}
        <div className="text-center space-y-2">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-indigo-600 shadow-lg">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="w-7 h-7 text-white"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.8}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z"
              />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-gray-900">Tax Agent</h1>
          <p className="text-sm text-gray-500">
            Secure AI-powered tax document extraction
          </p>
        </div>

        {/* Error banner */}
        {errorMsg && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {errorMsg}
          </div>
        )}

        {/* Sign-in card */}
        <div className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm space-y-5">
          <p className="text-sm text-gray-600 text-center">
            Sign in with your organisation account to upload and process tax documents.
          </p>

          {/*
            This <a> hits /api/auth/login which is a server-side redirect
            to the Cognito Hosted UI. No JavaScript auth code runs here.
          */}
          <a
            href="/api/auth/login"
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white shadow hover:bg-indigo-700 active:bg-indigo-800 transition-colors"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-5 w-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
            </svg>
            Sign in with Cognito
          </a>

          <p className="text-center text-xs text-gray-400">
            Access is restricted to authorised users only.
            <br />
            Contact your administrator to request access.
          </p>
        </div>

        <p className="text-center text-xs text-gray-400">
          Protected by AWS Cognito · Data encrypted with KMS
        </p>
      </div>
    </main>
  );
}
