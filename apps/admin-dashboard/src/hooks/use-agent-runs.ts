import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api/client";
import { queryKeys } from "@/hooks/query-keys";
import type { AgentRun, PaginatedResponse } from "@/lib/api/types";

// Agent runs move through pending/running/succeeded/failed on their own
// timeline (a collector or reviewer graph running in the worker
// process) — poll while any run on the current page is still in flight
// so the status view reflects progress without the user manually
// refreshing, and stop polling once nothing on the page is active.
const ACTIVE_STATUSES = new Set(["pending", "running"]);

export function useAgentRuns(page: number, pageSize = 20) {
  return useQuery({
    queryKey: queryKeys.agentRuns.list(page),
    queryFn: () =>
      apiGet<PaginatedResponse<AgentRun>>("agents/runs", { page, page_size: pageSize }),
    refetchInterval: (query) => {
      const data = query.state.data;
      const hasActiveRun = data?.items.some((run) => ACTIVE_STATUSES.has(run.status)) ?? false;
      return hasActiveRun ? 5000 : false;
    },
  });
}

export function useAgentRun(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.agentRuns.detail(id ?? ""),
    queryFn: () => apiGet<AgentRun>(`agents/runs/${id}`),
    enabled: id !== undefined,
    refetchInterval: (query) =>
      query.state.data && ACTIVE_STATUSES.has(query.state.data.status) ? 3000 : false,
  });
}
