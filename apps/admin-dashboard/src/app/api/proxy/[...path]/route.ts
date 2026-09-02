import { NextRequest } from "next/server";
import { proxiedFetch } from "@/lib/auth/authenticated-fetch";

// Generic authenticated proxy: every client-side data hook in this
// dashboard calls a relative /api/proxy/<fastapi-path> URL rather than
// FastAPI directly, so the browser never holds the bearer token — this
// route reads it off the httpOnly cookie server-side (see
// src/lib/auth/authenticated-fetch.ts for the actual forward + 401
// refresh-and-retry logic) and forwards the request to FastAPI's
// /api/v1/* surface with the query string preserved.
//
// No business logic here — this is a pure pass-through. Response
// bodies/status/errors are exactly what FastAPI returned.

async function handle(request: NextRequest, path: string[]) {
  const backendPath = `/api/v1/${path.join("/")}${request.nextUrl.search}`;
  const method = request.method;
  const hasBody = method !== "GET" && method !== "HEAD" && method !== "DELETE";

  return proxiedFetch(request, backendPath, {
    method,
    headers: hasBody ? { "Content-Type": "application/json" } : undefined,
    body: hasBody ? await request.text() : undefined,
  });
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return handle(request, (await params).path);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return handle(request, (await params).path);
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return handle(request, (await params).path);
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return handle(request, (await params).path);
}
