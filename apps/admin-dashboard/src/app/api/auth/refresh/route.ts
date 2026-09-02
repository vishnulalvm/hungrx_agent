import { NextRequest, NextResponse } from "next/server";
import { refreshTokens } from "@/lib/auth/refresh";
import {
  REFRESH_TOKEN_COOKIE,
  accessTokenCookie,
  clearedAuthCookies,
  refreshTokenCookie,
} from "@/lib/auth/cookies";

export async function POST(request: NextRequest) {
  const refreshToken = request.cookies.get(REFRESH_TOKEN_COOKIE)?.value;
  if (!refreshToken) {
    return NextResponse.json(
      { error: { code: "unauthorized", message: "No session" } },
      { status: 401 },
    );
  }

  const tokens = await refreshTokens(refreshToken);
  if (!tokens) {
    const response = NextResponse.json(
      { error: { code: "unauthorized", message: "Session expired" } },
      { status: 401 },
    );
    for (const cookie of clearedAuthCookies()) {
      response.cookies.set(cookie);
    }
    return response;
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set(accessTokenCookie(tokens.access_token));
  response.cookies.set(refreshTokenCookie(tokens.refresh_token));
  return response;
}
