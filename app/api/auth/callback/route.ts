/**
 * GET /api/auth/callback
 *
 * Cognito Hosted UI redirects here after successful authentication.
 * This handler:
 *  1. Validates the state parameter (CSRF guard).
 *  2. Exchanges the authorization code + PKCE verifier for tokens.
 *  3. Sets access_token, id_token, and refresh_token in httpOnly cookies.
 *  4. Clears the one-time-use PKCE and state cookies.
 *  5. Redirects to the app root (or the originally-requested page).
 */

import { type NextRequest, NextResponse } from 'next/server';
import { exchangeCodeForTokens } from '@/lib/cognito';
import { COOKIE, COOKIE_OPTS } from '@/lib/auth';

export const runtime = 'nodejs';

// Access token cookie lifetime (matches Cognito setting: 1 hour).
const ACCESS_TOKEN_MAX_AGE  = 60 * 60;          // 1 hour
// Refresh token cookie lifetime (matches Cognito setting: 30 days).
const REFRESH_TOKEN_MAX_AGE = 60 * 60 * 24 * 30; // 30 days

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl;
  const code  = searchParams.get('code');
  const state = searchParams.get('state');
  const error = searchParams.get('error');

  // Cognito returns an error param when the user cancels or auth fails.
  if (error) {
    const description = searchParams.get('error_description') ?? error;
    const loginUrl = new URL('/login', req.url);
    loginUrl.searchParams.set('error', description);
    return NextResponse.redirect(loginUrl);
  }

  if (!code || !state) {
    return NextResponse.redirect(new URL('/login?error=missing_params', req.url));
  }

  // ── CSRF check ─────────────────────────────────────────────────────────────
  const storedState    = req.cookies.get(COOKIE.OAUTH_STATE)?.value;
  const codeVerifier   = req.cookies.get(COOKIE.PKCE_VERIFIER)?.value;

  if (!storedState || !codeVerifier) {
    // Cookies expired or were never set — restart the flow.
    return NextResponse.redirect(new URL('/login?error=session_expired', req.url));
  }

  if (state !== storedState) {
    // Possible CSRF attack — abort.
    return NextResponse.redirect(new URL('/login?error=state_mismatch', req.url));
  }

  // ── Token exchange ──────────────────────────────────────────────────────────
  let tokenSet;
  try {
    tokenSet = await exchangeCodeForTokens(code, codeVerifier);
  } catch (err) {
    console.error('[/api/auth/callback] token exchange failed:', err);
    return NextResponse.redirect(new URL('/login?error=token_exchange_failed', req.url));
  }

  // ── Set httpOnly auth cookies ───────────────────────────────────────────────
  const response = NextResponse.redirect(new URL('/', req.url));

  response.cookies.set(COOKIE.ACCESS_TOKEN, tokenSet.access_token, {
    ...COOKIE_OPTS,
    maxAge: ACCESS_TOKEN_MAX_AGE,
  });

  response.cookies.set(COOKIE.ID_TOKEN, tokenSet.id_token, {
    ...COOKIE_OPTS,
    maxAge: ACCESS_TOKEN_MAX_AGE,
  });

  if (tokenSet.refresh_token) {
    response.cookies.set(COOKIE.REFRESH_TOKEN, tokenSet.refresh_token, {
      ...COOKIE_OPTS,
      maxAge: REFRESH_TOKEN_MAX_AGE,
    });
  }

  // Clear the one-time PKCE flow cookies.
  response.cookies.delete(COOKIE.PKCE_VERIFIER);
  response.cookies.delete(COOKIE.OAUTH_STATE);

  return response;
}
