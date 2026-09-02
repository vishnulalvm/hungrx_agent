"use client";

import { useState } from "react";
import { useAgentRuns } from "@/hooks/use-agent-runs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import type { AgentRunStatus } from "@/lib/api/types";

const STATUS_VARIANT: Record<AgentRunStatus, "default" | "ok" | "attn" | "accent" | "outline"> = {
  pending: "outline",
  running: "accent",
  succeeded: "ok",
  failed: "attn",
  cancelled: "outline",
};

const WORKFLOW_LABEL: Record<string, string> = {
  collector_workflow: "Collector",
  reviewer_workflow: "Reviewer",
};

export default function AgentRunsPage() {
  const [page, setPage] = useState(1);
  const { data, isPending, isError, error, refetch, isFetching } = useAgentRuns(page);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="container py-8">
      <h1 className="text-xl font-bold tracking-tight text-ink">Agent Runs</h1>
      <p className="mt-1 text-sm text-ink-faint">
        LangGraph collector/reviewer workflow run history. Updates automatically while a run is in
        progress.
      </p>

      <div className="mt-6">
        {isPending ? (
          <TableSkeleton />
        ) : isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.items.length === 0 ? (
          <p className="text-[12.5px] text-ink-faint">No agent runs yet.</p>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Workflow</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Completed</TableHead>
                  <TableHead>Error</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell className="font-medium">
                      {WORKFLOW_LABEL[run.workflow_type] ?? run.workflow_type}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[run.status]}>{run.status}</Badge>
                    </TableCell>
                    <TableCell className="text-ink-faint">
                      {run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
                    </TableCell>
                    <TableCell className="text-ink-faint">
                      {run.completed_at ? new Date(run.completed_at).toLocaleString() : "—"}
                    </TableCell>
                    <TableCell className="max-w-xs truncate text-attn">
                      {run.error_message ?? ""}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            <div className="mt-4 flex items-center justify-between text-[12.5px] text-ink-faint">
              <span>
                Page {data.page} of {totalPages} · {data.total} total
                {isFetching && " · refreshing…"}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage((current) => current + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
