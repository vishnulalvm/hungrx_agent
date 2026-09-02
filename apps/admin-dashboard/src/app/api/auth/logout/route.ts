import { NextRequest, NextResponse } from "next/server";
import { backendBaseUrl } from "@/lib/api/backend-url";
import { REFRESH_TOKEN_COOKIE, clearedAuthCookies } from "@/lib/auth/cookies";

// Best-effort revoke server-side (FastAPI's /auth/logout revokes the
// specific refresh token), then always clear the cookies regardless of
// whether the backend call succeeded — a client that asked to log out
// should never be left holding a still-set session cookie just because
// the backend was briefly unreachable.
export async function POST(request: NextRequest) {
  const refreshToken = request.cookies.get(REFRESH_TOKEN_COOKIE)?.value;

  if (refreshToken) {
    try {
      await fetch(`${backendBaseUrl()}/api/v1/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
    } catch {
      // Deliberately swallowed — see module docstring.
    }
  }

  const response = NextResponse.json({ ok: true });
  for (const cookie of clearedAuthCookies()) {
    response.cookies.set(cookie);
  }
  return response;
}
