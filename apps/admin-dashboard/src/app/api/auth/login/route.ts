import { NextRequest, NextResponse } from "next/server";
import { backendBaseUrl } from "@/lib/api/backend-url";
import { accessTokenCookie, refreshTokenCookie } from "@/lib/auth/cookies";
import type { ApiErrorBody } from "@/lib/api/errors";

// Proxies to FastAPI's POST /auth/login and, on success, sets the
// returned access/refresh tokens as httpOnly cookies rather than
// returning them in the JSON body — the browser never holds the raw JWT
// in a place client-side JS (or an XSS payload) could read it.
export async function POST(request: NextRequest) {
  const body = await request.json();

  const backendResponse = await fetch(`${backendBaseUrl()}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!backendResponse.ok) {
    const errorBody = (await backendResponse.json()) as ApiErrorBody;
    return NextResponse.json(errorBody, { status: backendResponse.status });
  }

  const tokens = (await backendResponse.json()) as { access_token: string; refresh_token: string };

  const response = NextResponse.json({ ok: true });
  response.cookies.set(accessTokenCookie(tokens.access_token));
  response.cookies.set(refreshTokenCookie(tokens.refresh_token));
  return response;
}
