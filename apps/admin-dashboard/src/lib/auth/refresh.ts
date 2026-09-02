import { backendBaseUrl } from "@/lib/api/backend-url";

// Calls FastAPI's POST /auth/refresh. Refresh tokens are single-use on
// the backend (AuthService.refresh revokes the presented token and
// issues a brand-new pair — see apps/api/app/services/auth_service.py),
// so a caller MUST persist the returned pair as the new cookies; reusing
// the old refresh token a second time will fail.
export async function refreshTokens(
  refreshToken: string,
): Promise<{ access_token: string; refresh_token: string } | null> {
  const response = await fetch(`${backendBaseUrl()}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!response.ok) {
    return null;
  }

  return (await response.json()) as { access_token: string; refresh_token: string };
}
