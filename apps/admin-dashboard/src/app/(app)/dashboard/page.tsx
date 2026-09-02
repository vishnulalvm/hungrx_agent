"use client";

import Link from "next/link";
import { useRestaurants } from "@/hooks/use-restaurants";
import { usePendingReviews } from "@/hooks/use-reviews";
import { useAgentRuns } from "@/hooks/use-agent-runs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function StatCard({
  href,
  title,
  value,
  isLoading,
  hint,
}: {
  href: string;
  title: string;
  value: number | undefined;
  isLoading: boolean;
  hint?: string;
}) {
  return (
    <Link href={href}>
      <Card className="transition-colors hover:border-ink/20">
        <CardHeader>
          <CardTitle className="text-[12.5px] text-ink-faint">{title}</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-8 w-16" />
          ) : (
            <div className="text-2xl font-bold text-ink">{value}</div>
          )}
          {hint && <p className="mt-1 text-[11.5px] text-ink-faint">{hint}</p>}
        </CardContent>
      </Card>
    </Link>
  );
}

export default function DashboardPage() {
  const restaurants = useRestaurants(1);
  const pendingReviews = usePendingReviews();
  const agentRuns = useAgentRuns(1);

  const runningCount = agentRuns.data?.items.filter((run) => run.status === "running").length ?? 0;
  const failedCount = agentRuns.data?.items.filter((run) => run.status === "failed").length ?? 0;

  return (
    <div className="container py-8">
      <h1 className="text-xl font-bold tracking-tight text-ink">Dashboard</h1>
      <p className="mt-1 text-sm text-ink-faint">
        Overview of restaurant data, ingestion, and review activity.
      </p>

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          href="/restaurants"
          title="Published restaurants"
          value={restaurants.data?.total}
          isLoading={restaurants.isPending}
        />
        <StatCard
          href="/review-queue"
          title="Pending reviews"
          value={pendingReviews.data?.length}
          isLoading={pendingReviews.isPending}
        />
        <StatCard
          href="/agent-runs"
          title="Runs in progress"
          value={runningCount}
          isLoading={agentRuns.isPending}
          hint="on most recent page"
        />
        <StatCard
          href="/agent-runs"
          title="Recent failures"
          value={failedCount}
          isLoading={agentRuns.isPending}
          hint="on most recent page"
        />
      </div>
    </div>
  );
}
