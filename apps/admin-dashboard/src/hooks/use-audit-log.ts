import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api/client";
import { queryKeys } from "@/hooks/query-keys";
import type { AuditLogEntry } from "@/lib/api/types";

export function useAuditLog(limit = 100) {
  return useQuery({
    queryKey: queryKeys.auditLog.list(),
    queryFn: () => apiGet<AuditLogEntry[]>("admin/audit-log", { limit }),
  });
}
