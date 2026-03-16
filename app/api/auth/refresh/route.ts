/**
 * POST /api/auth/refresh
 *
 * Exchanges the refresh_token cookie for a new access token.
 * Called client-side when an API route returns { code: "TOKEN_EXPIRED" }.
 *
 * On success: sets a new access_token cookie and returns { ok: true }.
 * On failure: clears all auth cookies and returns 401 — the client
 *             should redirect the user to /login.
 */

import { type NextRequest, NextResponse } from 'next/server';
import { refreshAccessToken } from '@/lib/cognito';
import { COOKIE, COOKIE_OPTS } from '@/lib/auth';

export const runtime = 'nodejs';

const ACCESS_TOKEN_MAX_AGE = 60 * 60; // 1 hour

export async function POST(req: NextRequest) {
  const refreshToken = req.cookies.get(COOKIE.REFRESH_TOKEN)?.value;

  if (!refreshToken) {
    return NextResponse.json(
      { error: 'No refresh token', code: 'NO_REFRESH_TOKEN' },
      { status: 401 },
    );
  }

  try {
    const tokenSet = await refreshAccessToken(refreshToken);

    const response = NextResponse.json({ ok: true });
    response.cookies.set(COOKIE.ACCESS_TOKEN, tokenSet.access_token, {
      ...COOKIE_OPTS,
      maxAge: ACCESS_TOKEN_MAX_AGE,
    });
    // Cognito issues a new id_token on refresh too.
    if (tokenSet.id_token) {
      response.cookies.set(COOKIE.ID_TOKEN, tokenSet.id_token, {
        ...COOKIE_OPTS,
        maxAge: ACCESS_TOKEN_MAX_AGE,
      });
    }
    return response;
  } catch (err) {
    console.error('[/api/auth/refresh]', err);

    // Refresh token is invalid/revoked — force re-login.
    const response = NextResponse.json(
      { error: 'Session expired', code: 'REFRESH_FAILED' },
      { status: 401 },
    );
    for (const name of Object.values(COOKIE)) {
      response.cookies.set(name, '', { maxAge: 0, path: '/' });
    }
    return response;
  }
}
