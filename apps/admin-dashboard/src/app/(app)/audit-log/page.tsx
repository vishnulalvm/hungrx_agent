"use client";

import { useAuditLog } from "@/hooks/use-audit-log";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TableSkeleton } from "@/components/table-skeleton";
import { ErrorState } from "@/components/error-state";

export default function AuditLogPage() {
  const { data, isPending, isError, error, refetch } = useAuditLog();

  return (
    <div className="container py-8">
      <h1 className="text-xl font-bold tracking-tight text-ink">Audit Log</h1>
      <p className="mt-1 text-sm text-ink-faint">
        Every auditable action across the platform, most recent first.
      </p>

      <div className="mt-6">
        {isPending ? (
          <TableSkeleton rows={10} />
        ) : isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.length === 0 ? (
          <p className="text-[12.5px] text-ink-faint">No audit entries yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Action</TableHead>
                <TableHead>Entity</TableHead>
                <TableHead>Actor</TableHead>
                <TableHead>When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((entry) => (
                <TableRow key={entry.id}>
                  <TableCell>
                    <Badge>{entry.action}</Badge>
                  </TableCell>
                  <TableCell className="text-ink-soft">
                    {entry.entity_type} ·{" "}
                    <span className="font-mono text-[11px]">{entry.entity_id}</span>
                  </TableCell>
                  <TableCell className="text-ink-soft">{entry.actor_email ?? "system"}</TableCell>
                  <TableCell className="text-ink-faint">
                    {new Date(entry.created_at).toLocaleString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
