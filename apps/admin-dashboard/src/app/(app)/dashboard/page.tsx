"use client";

import Link from "next/link";
import {
  Building2,
  Clock,
  Loader2,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
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
  icon: Icon,
}: {
  href: string;
  title: string;
  value: number | undefined;
  isLoading: boolean;
  hint: string;
  icon: LucideIcon;
}) {
  return (
    <Link href={href} className="block h-full">
      <Card className="flex h-full min-h-[180px] flex-col justify-between p-2 transition-all hover:-translate-y-0.5 hover:border-ink/20 hover:shadow-sm">
        <CardHeader className="flex-row items-start justify-between gap-3 space-y-0 pb-0">
          <CardTitle className="text-[13px] font-medium uppercase leading-snug tracking-wide text-ink-faint">
            {title}
          </CardTitle>
          <Icon className="size-5 shrink-0 text-ink-faint/60" aria-hidden />
        </CardHeader>
        <CardContent className="flex flex-1 flex-col justify-end">
          {isLoading ? (
            <Skeleton className="h-10 w-20" />
          ) : (
            <div className="text-4xl font-bold tabular-nums text-ink">{value}</div>
          )}
          <p className="mt-2 text-[12.5px] text-ink-faint">{hint}</p>
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

      <div className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          href="/restaurants"
          title="Published restaurants"
          value={restaurants.data?.total}
          isLoading={restaurants.isPending}
          hint="Live in production"
          icon={Building2}
        />
        <StatCard
          href="/review-queue"
          title="Pending reviews"
          value={pendingReviews.data?.length}
          isLoading={pendingReviews.isPending}
          hint="Awaiting a decision"
          icon={Clock}
        />
        <StatCard
          href="/agent-runs"
          title="Runs in progress"
          value={runningCount}
          isLoading={agentRuns.isPending}
          hint="Most recent page"
          icon={Loader2}
        />
        <StatCard
          href="/agent-runs"
          title="Recent failures"
          value={failedCount}
          isLoading={agentRuns.isPending}
          hint="Most recent page"
          icon={TriangleAlert}
        />
      </div>
    </div>
  );
}
