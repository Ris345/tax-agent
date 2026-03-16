/**
 * JWT verification utilities for Cognito access tokens.
 *
 * Uses `jose` (Web Crypto API — Edge Runtime compatible).
 * The JWKS remote set is lazily initialised and then cached at module scope,
 * so JWKS keys are only fetched once per Edge worker lifetime.
 *
 * Cognito access-token shape (RS256):
 *   iss  = https://cognito-idp.{region}.amazonaws.com/{userPoolId}
 *   sub  = user UUID  ← this is the stable user_id we propagate downstream
 *   token_use = "access"   ← MUST verify; ID tokens share the same JWKS
 *   client_id = Cognito App Client ID  ← MUST verify to reject tokens issued
 *                                         to other clients in the same pool
 *   exp / iat  — verified automatically by jose
 */

import { createRemoteJWKSet, jwtVerify, type JWTPayload } from 'jose';

// ── Cognito claim extensions ────────────────────────────────────────────────

export interface CognitoAccessTokenPayload extends JWTPayload {
  /** Cognito user UUID — stable, never reassigned (use as user_id). */
  sub: string;
  /** Always "access" for access tokens. */
  token_use: 'access';
  /** Cognito App Client ID. */
  client_id: string;
  /** Cognito username (may differ from sub; avoid using as a stable ID). */
  username: string;
  /** Space-delimited OAuth scopes granted. */
  scope: string;
  /** Email from user pool attributes (present when email scope granted). */
  email?: string;
}

// ── Lazy JWKS initialisation ────────────────────────────────────────────────
// createRemoteJWKSet handles HTTP caching of the JWKS document internally.

type RemoteJWKSet = ReturnType<typeof createRemoteJWKSet>;
let _jwks: RemoteJWKSet | null = null;

function getJwks(): RemoteJWKSet {
  if (_jwks) return _jwks;

  const userPoolId = process.env.COGNITO_USER_POOL_ID;
  const region = process.env.COGNITO_REGION ?? 'us-east-1';

  if (!userPoolId) {
    throw new Error('COGNITO_USER_POOL_ID environment variable is not set');
  }

  const jwksUrl = `https://cognito-idp.${region}.amazonaws.com/${userPoolId}/.well-known/jwks.json`;
  _jwks = createRemoteJWKSet(new URL(jwksUrl));
  return _jwks;
}

// ── Public API ──────────────────────────────────────────────────────────────

/**
 * Verify a Cognito access token and return its typed payload.
 *
 * Throws on:
 *  - Invalid/malformed JWT
 *  - Expired token (exp)
 *  - Wrong issuer
 *  - token_use ≠ "access" (rejects ID tokens masquerading as access tokens)
 *  - client_id mismatch (rejects tokens from other Cognito app clients)
 */
export async function verifyAccessToken(
  token: string,
): Promise<CognitoAccessTokenPayload> {
  const region = process.env.COGNITO_REGION ?? 'us-east-1';
  const userPoolId = process.env.COGNITO_USER_POOL_ID!;
  const clientId = process.env.COGNITO_CLIENT_ID!;

  const issuer = `https://cognito-idp.${region}.amazonaws.com/${userPoolId}`;

  const { payload } = await jwtVerify(token, getJwks(), {
    issuer,
    // Cognito access tokens have `client_id` not `aud`; pass no `audience`
    // so jose does not reject on a missing `aud` claim.
    algorithms: ['RS256'],
  });

  const claims = payload as CognitoAccessTokenPayload;

  // Explicitly check token_use — critical to prevent ID token substitution.
  if (claims.token_use !== 'access') {
    throw new Error(`Invalid token_use: expected "access", got "${claims.token_use}"`);
  }

  // Prevent tokens issued to a different app client in the same pool.
  if (claims.client_id !== clientId) {
    throw new Error(`client_id mismatch: expected "${clientId}", got "${claims.client_id}"`);
  }

  return claims;
}

/**
 * Extract the bearer token from an Authorization header.
 * Returns undefined if the header is absent or malformed.
 */
export function extractBearerToken(authHeader: string | null): string | undefined {
  if (!authHeader?.startsWith('Bearer ')) return undefined;
  return authHeader.slice(7);
}

// ── Cookie helpers ──────────────────────────────────────────────────────────

/** Names of the httpOnly auth cookies. */
export const COOKIE = {
  ACCESS_TOKEN:   'tax_access_token',
  REFRESH_TOKEN:  'tax_refresh_token',
  ID_TOKEN:       'tax_id_token',
  OAUTH_STATE:    'tax_oauth_state',
  PKCE_VERIFIER:  'tax_pkce_verifier',
} as const;

/** Cookie attributes shared by all auth cookies. */
export const COOKIE_OPTS = {
  httpOnly: true,
  secure: process.env.NODE_ENV === 'production',
  sameSite: 'lax' as const,
  path: '/',
} as const;

/** Short-lived cookie options for the OAuth flow intermediary cookies. */
export const FLOW_COOKIE_OPTS = {
  ...COOKIE_OPTS,
  maxAge: 60 * 10,        // 10 minutes — enough to complete the PKCE round-trip
} as const;
