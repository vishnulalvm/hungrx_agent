import { ApiError, type ApiErrorBody } from "@/lib/api/errors";

// Every client-side call in this dashboard goes through this one
// function, which hits this Next.js app's own /api/proxy/<path> route
// (never FastAPI directly — see src/app/api/proxy/[...path]/route.ts)
// so the bearer token stays server-side. Parses FastAPI's error envelope
// into a typed ApiError on any non-2xx response.
export async function apiFetch<T>(
  path: string,
  init: RequestInit & { params?: Record<string, string | number | undefined> } = {},
): Promise<T> {
  const { params, ...requestInit } = init;

  let url = `/api/proxy/${path.replace(/^\/+/, "")}`;
  if (params) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) query.set(key, String(value));
    }
    const queryString = query.toString();
    if (queryString) url += `?${queryString}`;
  }

  const response = await fetch(url, requestInit);

  if (response.status === 204) {
    return undefined as T;
  }

  const body = await response.json();

  if (!response.ok) {
    throw new ApiError(response.status, body as ApiErrorBody);
  }

  return body as T;
}

export function apiGet<T>(
  path: string,
  params?: Record<string, string | number | undefined>,
): Promise<T> {
  return apiFetch<T>(path, { method: "GET", params });
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
