// Central query-key factory so invalidation/optimistic updates in one
// hook file never drift from the key another hook file reads with.
export const queryKeys = {
  restaurants: {
    list: (page: number) => ["restaurants", "list", page] as const,
    detail: (id: string) => ["restaurants", "detail", id] as const,
  },
  reviews: {
    pending: () => ["reviews", "pending"] as const,
    detail: (id: string) => ["reviews", "detail", id] as const,
  },
  agentRuns: {
    list: (page: number) => ["agent-runs", "list", page] as const,
    detail: (id: string) => ["agent-runs", "detail", id] as const,
  },
  auditLog: {
    list: () => ["audit-log", "list"] as const,
  },
};
