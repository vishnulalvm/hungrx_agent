import { NextRequest, NextResponse } from "next/server";
import { backendBaseUrl } from "@/lib/api/backend-url";
import { refreshTokens } from "@/lib/auth/refresh";
import {
  ACCESS_TOKEN_COOKIE,
  REFRESH_TOKEN_COOKIE,
  accessTokenCookie,
  clearedAuthCookies,
  refreshTokenCookie,
} from "@/lib/auth/cookies";

// Shared by /api/auth/me and /api/proxy/[...path]: attach the
// access-token cookie as a Bearer header, call FastAPI, and — on a 401
// only (meaning the access token itself is the problem, not a
// permission/business-logic 403 further downstream) — transparently
// refresh once and retry exactly once before giving up. A second 401
// after a successful refresh is treated as a genuine auth failure, not
// looped on, since refresh tokens are single-use (retrying with the same
// old token would just fail again).
export async function proxiedFetch(
  request: NextRequest,
  backendPath: string,
  init: RequestInit = {},
): Promise<NextResponse> {
  const accessToken = request.cookies.get(ACCESS_TOKEN_COOKIE)?.value;
  if (!accessToken) {
    return unauthorized();
  }

  let backendResponse = await fetch(`${backendBaseUrl()}${backendPath}`, {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${accessToken}` },
  });

  if (backendResponse.status !== 401) {
    return await passthrough(backendResponse);
  }

  const refreshToken = request.cookies.get(REFRESH_TOKEN_COOKIE)?.value;
  if (!refreshToken) {
    return unauthorized();
  }

  const tokens = await refreshTokens(refreshToken);
  if (!tokens) {
    return unauthorized({ clearCookies: true });
  }

  backendResponse = await fetch(`${backendBaseUrl()}${backendPath}`, {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${tokens.access_token}` },
  });

  const response = await passthrough(backendResponse);
  response.cookies.set(accessTokenCookie(tokens.access_token));
  response.cookies.set(refreshTokenCookie(tokens.refresh_token));
  return response;
}

async function passthrough(backendResponse: Response): Promise<NextResponse> {
  if (backendResponse.status === 204) {
    return new NextResponse(null, { status: 204 });
  }
  const body = await backendResponse.text();
  return new NextResponse(body, {
    status: backendResponse.status,
    headers: { "Content-Type": backendResponse.headers.get("Content-Type") ?? "application/json" },
  });
}

function unauthorized(options: { clearCookies?: boolean } = {}): NextResponse {
  const response = NextResponse.json(
    { error: { code: "unauthorized", message: "Session expired" } },
    { status: 401 },
  );
  if (options.clearCookies) {
    for (const cookie of clearedAuthCookies()) {
      response.cookies.set(cookie);
    }
  }
  return response;
}
