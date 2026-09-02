import { NextRequest } from "next/server";
import { proxiedFetch } from "@/lib/auth/authenticated-fetch";

// The one place a client component learns "who is logged in" — proxies
// to FastAPI's GET /auth/me with the access-token cookie's refresh-and-
// retry-once behavior already handled by proxiedFetch.
export async function GET(request: NextRequest) {
  return proxiedFetch(request, "/api/v1/auth/me");
}
