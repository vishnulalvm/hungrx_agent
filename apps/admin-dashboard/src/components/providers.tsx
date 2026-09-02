"use client";

import { useState } from "react";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { AuthProvider } from "@/lib/auth/auth-context";
import { isApiError } from "@/lib/api/errors";

export function Providers({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60 * 1000,
            retry: 1,
          },
        },
        // /api/proxy already refreshes the access token once and retries
        // before ever surfacing a 401 to the client (see
        // src/lib/auth/authenticated-fetch.ts) — a 401 reaching here means
        // the refresh token itself is gone/expired, i.e. the session is
        // genuinely over, so every query/mutation shares one redirect
        // instead of each screen implementing its own.
        queryCache: new QueryCache({
          onError: (error) => {
            if (isApiError(error) && error.status === 401) {
              router.push("/login");
            }
          },
        }),
        mutationCache: new MutationCache({
          onError: (error) => {
            if (isApiError(error) && error.status === 401) {
              router.push("/login");
            }
          },
        }),
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}
