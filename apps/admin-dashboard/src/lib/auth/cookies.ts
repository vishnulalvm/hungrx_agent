// Server-only cookie names/options for the access/refresh JWTs. Read and
// written exclusively from Next.js Route Handlers and middleware.ts —
// client components never see the raw token (httpOnly), only the fact
// that a session cookie is present.

export const ACCESS_TOKEN_COOKIE = "hungrx_access_token";
export const REFRESH_TOKEN_COOKIE = "hungrx_refresh_token";

// Mirrors core/config/settings.py's jwt_access_token_expire_minutes (60)
// / jwt_refresh_token_expire_minutes (14 days) defaults. The cookie's own
// maxAge is a client-side convenience (browser stops sending it once
// expired) — the backend independently rejects an expired/invalid JWT
// regardless of what the cookie says, so this never needs to be kept in
// perfect sync with the backend's actual settings.
const ACCESS_TOKEN_MAX_AGE_SECONDS = 60 * 60;
const REFRESH_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 14;

const isProduction = process.env.NODE_ENV === "production";

export interface AuthCookie {
  name: string;
  value: string;
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  maxAge: number;
}

function baseCookieOptions(): Omit<AuthCookie, "name" | "value" | "maxAge"> {
  return {
    httpOnly: true,
    secure: isProduction,
    sameSite: "lax",
    path: "/",
  };
}

export function accessTokenCookie(value: string): AuthCookie {
  return {
    name: ACCESS_TOKEN_COOKIE,
    value,
    ...baseCookieOptions(),
    maxAge: ACCESS_TOKEN_MAX_AGE_SECONDS,
  };
}

export function refreshTokenCookie(value: string): AuthCookie {
  return {
    name: REFRESH_TOKEN_COOKIE,
    value,
    ...baseCookieOptions(),
    maxAge: REFRESH_TOKEN_MAX_AGE_SECONDS,
  };
}

export function clearedAuthCookies(): AuthCookie[] {
  return [
    { name: ACCESS_TOKEN_COOKIE, value: "", ...baseCookieOptions(), maxAge: 0 },
    { name: REFRESH_TOKEN_COOKIE, value: "", ...baseCookieOptions(), maxAge: 0 },
  ];
}
