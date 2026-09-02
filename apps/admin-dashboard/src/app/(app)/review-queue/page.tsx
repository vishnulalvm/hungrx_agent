"use client";

import Link from "next/link";
import { usePendingReviews } from "@/hooks/use-reviews";
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

const ENTITY_LABEL: Record<string, string> = {
  restaurant: "Restaurant",
  restaurant_location: "Location",
  menu: "Menu",
  menu_category: "Menu category",
  dish: "Dish",
};

export default function ReviewQueuePage() {
  const { data, isPending, isError, error, refetch } = usePendingReviews();

  return (
    <div className="container py-8">
      <h1 className="text-xl font-bold tracking-tight text-ink">Review Queue</h1>
      <p className="mt-1 text-sm text-ink-faint">
        Collector and reviewer workflow runs paused for human approval.
      </p>

      <div className="mt-6">
        {isPending ? (
          <TableSkeleton />
        ) : isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.length === 0 ? (
          <p className="text-[12.5px] text-ink-faint">Nothing pending review.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Entity</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Submitted</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((review) => (
                <TableRow key={review.id}>
                  <TableCell>
                    <Link
                      href={`/review-queue/${review.id}`}
                      className="font-medium hover:underline"
                    >
                      {ENTITY_LABEL[review.entity_type] ?? review.entity_type}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Badge variant="accent">{review.status}</Badge>
                  </TableCell>
                  <TableCell className="text-ink-faint">
                    {new Date(review.created_at).toLocaleString()}
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
