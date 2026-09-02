"use client";

import { useState } from "react";
import Link from "next/link";
import { useRestaurants } from "@/hooks/use-restaurants";
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

export default function RestaurantsPage() {
  const [page, setPage] = useState(1);
  const { data, isPending, isError, error, refetch, isFetching } = useRestaurants(page);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="container py-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-ink">Restaurants</h1>
          <p className="mt-1 text-sm text-ink-faint">Published restaurant data.</p>
        </div>
        <Link href="/ingestion">
          <Button>Ingest a restaurant</Button>
        </Link>
      </div>

      <div className="mt-6">
        {isPending ? (
          <TableSkeleton />
        ) : isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.items.length === 0 ? (
          <p className="text-[12.5px] text-ink-faint">
            No restaurants published yet. Trigger ingestion to get started.
          </p>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>City</TableHead>
                  <TableHead>Menu items</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Published</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((restaurant) => (
                  <TableRow key={restaurant.id}>
                    <TableCell>
                      <Link
                        href={`/restaurants/${restaurant.id}`}
                        className="font-medium hover:underline"
                      >
                        {restaurant.name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-ink-soft">{restaurant.city ?? "—"}</TableCell>
                    <TableCell className="text-ink-soft">{restaurant.menu_item_count}</TableCell>
                    <TableCell>
                      <Badge variant={restaurant.is_active ? "ok" : "outline"}>
                        {restaurant.is_active ? "Active" : "Inactive"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-ink-faint">
                      {restaurant.created_at
                        ? new Date(restaurant.created_at).toLocaleDateString()
                        : "—"}
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
