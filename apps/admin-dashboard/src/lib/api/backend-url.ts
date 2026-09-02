// Server-only: the FastAPI base URL Route Handlers/middleware talk to
// directly. Distinct from NEXT_PUBLIC_API_BASE_URL (unused by this
// dashboard's own client code, since every client-side call goes through
// this Next.js app's own /api/proxy route rather than hitting FastAPI
// directly — see src/app/api/proxy/[...path]/route.ts) — this one is
// never bundled for the browser. Falls back to the docker-compose
// service name so the default "just works" inside the compose network;
// override via API_INTERNAL_BASE_URL for any other deployment topology.

export function backendBaseUrl(): string {
  return process.env.API_INTERNAL_BASE_URL ?? "http://api:8000";
}
