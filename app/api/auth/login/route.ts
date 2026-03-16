/**
 * GET /api/auth/login
 *
 * Initiates the Cognito Hosted UI authorization code + PKCE flow:
 *  1. Generates a code verifier (PKCE) and an opaque state value (CSRF guard).
 *  2. Stores both in short-lived httpOnly cookies.
 *  3. Redirects the browser to the Cognito Hosted UI.
 *
 * The browser lands here after the user clicks "Sign in" on /login.
 * This route intentionally has NO authentication requirement — it is listed
 * in the middleware's PUBLIC_PREFIXES (/api/auth/*).
 */

import { type NextRequest, NextResponse } from 'next/server';
import {
  generateCodeVerifier,
  generateCodeChallenge,
  generateOAuthState,
  buildAuthorizationUrl,
} from '@/lib/cognito';
import { COOKIE, FLOW_COOKIE_OPTS } from '@/lib/auth';

export const runtime = 'nodejs'; // needs Node.js crypto

export async function GET(_req: NextRequest) {
  const codeVerifier    = generateCodeVerifier();
  const codeChallenge   = generateCodeChallenge(codeVerifier);
  const state           = generateOAuthState();
  const authorizationUrl = buildAuthorizationUrl(codeChallenge, state);

  const response = NextResponse.redirect(authorizationUrl);

  // Store intermediary values; cleared by the callback handler.
  response.cookies.set(COOKIE.PKCE_VERIFIER, codeVerifier, FLOW_COOKIE_OPTS);
  response.cookies.set(COOKIE.OAUTH_STATE,   state,         FLOW_COOKIE_OPTS);

  return response;
}
