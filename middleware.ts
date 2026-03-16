/**
 * Next.js Edge Middleware — JWT enforcement for all protected routes.
 *
 * Runs at the Edge layer (before API routes and server components).
 * Validates the Cognito access token from the httpOnly cookie, then:
 *  - Strips any client-supplied x-user-id / x-user-email headers (forgery prevention)
 *  - Sets x-user-id  = payload.sub   (stable Cognito UUID → downstream user_id)
 *  - Sets x-user-email = payload.email (informational only)
 *
 * API routes MUST read user_id from the x-user-id header only — never from
 * the request body or query string.
 *
 * Public routes (no token required):
 *   /login                  — login page
 *   /api/auth/*             — PKCE initiation, callback, logout, refresh
 *   /_next/*                — Next.js internals
 *   /favicon.ico
 */

import { type NextRequest, NextResponse } from 'next/server';
import { verifyAccessToken, COOKIE } from '@/lib/auth';

// ── Route classification ──────────────────────────────────────────────────────

const PUBLIC_PREFIXES = [
  '/login',
  '/api/auth/',
  '/_next/',
  '/favicon.ico',
];

function isPublic(pathname: string): boolean {
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

function isApiRoute(pathname: string): boolean {
  return pathname.startsWith('/api/');
}

// ── Middleware ────────────────────────────────────────────────────────────────

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (isPublic(pathname)) {
    return NextResponse.next();
  }

  const accessToken = req.cookies.get(COOKIE.ACCESS_TOKEN)?.value;

  // ── No token present ────────────────────────────────────────────────────────
  if (!accessToken) {
    if (isApiRoute(pathname)) {
      return NextResponse.json(
        { error: 'Unauthorized', code: 'NO_TOKEN' },
        { status: 401 },
      );
    }
    return NextResponse.redirect(new URL('/login', req.url));
  }

  // ── Validate token ──────────────────────────────────────────────────────────
  try {
    const payload = await verifyAccessToken(accessToken);

    // Sanitise inbound headers: strip any user-supplied values before
    // setting our trusted server-side values. A client cannot bypass this
    // because middleware runs before the route handler.
    const upstream = new Headers(req.headers);
    upstream.delete('x-user-id');    // prevent header injection
    upstream.delete('x-user-email');

    upstream.set('x-user-id',    payload.sub);
    upstream.set('x-user-email', payload.email ?? '');

    return NextResponse.next({ request: { headers: upstream } });
  } catch (err) {
    const isExpired =
      err instanceof Error &&
      (err.message.includes('"exp"') || err.message.toLowerCase().includes('expired'));

    if (isApiRoute(pathname)) {
      return NextResponse.json(
        {
          error: 'Unauthorized',
          code: isExpired ? 'TOKEN_EXPIRED' : 'INVALID_TOKEN',
        },
        { status: 401 },
      );
    }

    // Page route: clear the stale cookie and redirect to login.
    const loginUrl = new URL('/login', req.url);
    const response = NextResponse.redirect(loginUrl);
    response.cookies.delete(COOKIE.ACCESS_TOKEN);
    return response;
  }
}

export const config = {
  /*
   * Match everything except Next.js static assets and image optimisation.
   * The PUBLIC_PREFIXES check above handles further exemptions at runtime.
   */
  matcher: ['/((?!_next/static|_next/image|.*\\.(?:ico|png|svg|jpg|jpeg|webp)$).*)'],
};
