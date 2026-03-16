/**
 * GET /api/auth/logout
 *
 * Clears all auth cookies and redirects to the Cognito Hosted UI logout
 * endpoint, which invalidates the Cognito session and then redirects back
 * to the configured logout_uri (/login).
 */

import { type NextRequest, NextResponse } from 'next/server';
import { buildLogoutUrl } from '@/lib/cognito';
import { COOKIE } from '@/lib/auth';

export const runtime = 'nodejs';

export async function GET(_req: NextRequest) {
  const response = NextResponse.redirect(buildLogoutUrl());

  // Expire all auth cookies immediately.
  for (const name of Object.values(COOKIE)) {
    response.cookies.set(name, '', { maxAge: 0, path: '/' });
  }

  return response;
}
