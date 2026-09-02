import { NextRequest, NextResponse } from "next/server";
import { ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE } from "@/lib/auth/cookies";

// Server-side route gate: redirects to /login before any protected page
// ever renders (no client-side flash of protected content) if neither
// auth cookie is present. Deliberately only checks presence, not
// validity — an expired/invalid access token still reaches the page,
// where the data hooks' own 401 handling (via /api/proxy's refresh-and-
// retry, and useAuth's redirect-on-401 fallback) takes over; middleware
// can't itself call the refresh endpoint without adding a network round
// trip to every navigation.
const PUBLIC_PATHS = ["/login"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (PUBLIC_PATHS.some((path) => pathname.startsWith(path)) || pathname.startsWith("/api/")) {
    return NextResponse.next();
  }

  const hasSession =
    request.cookies.has(ACCESS_TOKEN_COOKIE) || request.cookies.has(REFRESH_TOKEN_COOKIE);

  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
