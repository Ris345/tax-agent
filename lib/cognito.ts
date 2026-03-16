/**
 * Cognito OAuth 2.0 + PKCE helpers.
 *
 * Runs in Node.js (API routes) — NOT the Edge runtime.
 * Uses Node.js `crypto` for PKCE generation and the Cognito token endpoint
 * for code exchange / refresh.
 */

import { createHash, randomBytes } from 'crypto';

// ── Config helpers ──────────────────────────────────────────────────────────

function required(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Environment variable ${name} is not set`);
  return v;
}

export function cognitoConfig() {
  const region = process.env.COGNITO_REGION ?? 'us-east-1';
  return {
    region,
    userPoolId:   required('COGNITO_USER_POOL_ID'),
    clientId:     required('COGNITO_CLIENT_ID'),
    clientSecret: process.env.COGNITO_CLIENT_SECRET ?? '', // empty → public client
    domain:       required('COGNITO_DOMAIN'),             // e.g. https://tax-agent-123.auth.us-east-1.amazoncognito.com
    redirectUri:  required('COGNITO_REDIRECT_URI'),       // e.g. https://app.example.com/api/auth/callback
    logoutUri:    process.env.COGNITO_LOGOUT_URI ?? required('COGNITO_REDIRECT_URI').replace('/api/auth/callback', '/login'),
  };
}

// ── PKCE ────────────────────────────────────────────────────────────────────

/** RFC 7636 — generate a high-entropy code verifier (43-128 chars, base64url). */
export function generateCodeVerifier(): string {
  return randomBytes(48).toString('base64url');    // 64 base64url chars
}

/** SHA-256 hash of the verifier, base64url-encoded (S256 method). */
export function generateCodeChallenge(verifier: string): string {
  return createHash('sha256').update(verifier).digest('base64url');
}

/** Cryptographically random opaque state value for CSRF protection. */
export function generateOAuthState(): string {
  return randomBytes(24).toString('base64url');
}

// ── Authorization URL ───────────────────────────────────────────────────────

/**
 * Build the Cognito Hosted UI authorization URL with PKCE.
 *
 * The caller must:
 *  1. Store `codeVerifier` in an httpOnly cookie before redirecting.
 *  2. Store `state` in an httpOnly cookie and verify it on callback.
 */
export function buildAuthorizationUrl(codeChallenge: string, state: string): string {
  const cfg = cognitoConfig();
  const params = new URLSearchParams({
    response_type:         'code',
    client_id:             cfg.clientId,
    redirect_uri:          cfg.redirectUri,
    scope:                 'openid email profile',
    state,
    code_challenge:        codeChallenge,
    code_challenge_method: 'S256',
  });
  return `${cfg.domain}/oauth2/authorize?${params.toString()}`;
}

// ── Token exchange ──────────────────────────────────────────────────────────

export interface TokenSet {
  access_token:  string;
  id_token:      string;
  refresh_token?: string;
  token_type:    string;
  expires_in:    number;
}

/**
 * Exchange an authorization code (+ PKCE verifier) for tokens.
 * Throws on HTTP error or Cognito error response.
 */
export async function exchangeCodeForTokens(
  code: string,
  codeVerifier: string,
): Promise<TokenSet> {
  const cfg = cognitoConfig();

  const body = new URLSearchParams({
    grant_type:    'authorization_code',
    client_id:     cfg.clientId,
    redirect_uri:  cfg.redirectUri,
    code,
    code_verifier: codeVerifier,
  });

  // Confidential client: add HTTP Basic auth with client_secret.
  const headers: Record<string, string> = {
    'Content-Type': 'application/x-www-form-urlencoded',
  };
  if (cfg.clientSecret) {
    const credentials = Buffer.from(`${cfg.clientId}:${cfg.clientSecret}`).toString('base64');
    headers['Authorization'] = `Basic ${credentials}`;
  }

  const resp = await fetch(`${cfg.domain}/oauth2/token`, {
    method:  'POST',
    headers,
    body:    body.toString(),
  });

  const data = await resp.json() as Record<string, unknown>;
  if (!resp.ok || data.error) {
    throw new Error(`Cognito token exchange failed: ${data.error ?? resp.status} — ${data.error_description ?? ''}`);
  }

  return data as unknown as TokenSet;
}

/**
 * Use a refresh token to obtain a new access (and optionally ID) token.
 * Cognito does NOT return a new refresh token on refresh — the existing one
 * remains valid until it expires or is revoked.
 */
export async function refreshAccessToken(refreshToken: string): Promise<TokenSet> {
  const cfg = cognitoConfig();

  const body = new URLSearchParams({
    grant_type:    'refresh_token',
    client_id:     cfg.clientId,
    refresh_token: refreshToken,
  });

  const headers: Record<string, string> = {
    'Content-Type': 'application/x-www-form-urlencoded',
  };
  if (cfg.clientSecret) {
    const credentials = Buffer.from(`${cfg.clientId}:${cfg.clientSecret}`).toString('base64');
    headers['Authorization'] = `Basic ${credentials}`;
  }

  const resp = await fetch(`${cfg.domain}/oauth2/token`, {
    method:  'POST',
    headers,
    body:    body.toString(),
  });

  const data = await resp.json() as Record<string, unknown>;
  if (!resp.ok || data.error) {
    throw new Error(`Cognito token refresh failed: ${data.error ?? resp.status}`);
  }

  return data as unknown as TokenSet;
}

/** Build the Cognito Hosted UI logout URL (clears Cognito session). */
export function buildLogoutUrl(): string {
  const cfg = cognitoConfig();
  const params = new URLSearchParams({
    client_id: cfg.clientId,
    logout_uri: cfg.logoutUri,
  });
  return `${cfg.domain}/logout?${params.toString()}`;
}
