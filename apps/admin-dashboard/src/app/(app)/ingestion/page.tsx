"use client";

import { useRef, useState } from "react";
import {
  useDeleteIngestionQueueItem,
  useIngestionQueueList,
  useRequeueIngestionQueueItem,
  useUpdateIngestionQueueItem,
  useUploadIngestionBatch,
} from "@/hooks/use-ingestion-queue";
import { isApiError } from "@/lib/api/errors";
import type { IngestionQueueItemInput, IngestionQueueItemSummary, IngestionQueueStatus } from "@/lib/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

const STATUS_BADGE: Record<string, { label: string; variant: "default" | "ok" | "attn" | "accent" }> = {
  queued: { label: "Queued", variant: "default" },
  running: { label: "Running", variant: "accent" },
  succeeded: { label: "Succeeded", variant: "ok" },
  failed: { label: "Failed", variant: "attn" },
};

const STATUS_FILTERS: { label: string; value: IngestionQueueStatus | undefined }[] = [
  { label: "All", value: undefined },
  { label: "Queued", value: "queued" },
  { label: "Running", value: "running" },
  { label: "Succeeded", value: "succeeded" },
  { label: "Failed", value: "failed" },
];

type SortOrder = "updated_desc" | "name_asc";

function parseBulkJson(raw: string): IngestionQueueItemInput[] {
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error("Expected a JSON array of restaurant rows");
  }
  return parsed;
}

export default function IngestionPage() {
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<IngestionQueueStatus | undefined>(undefined);
  const [sortOrder, setSortOrder] = useState<SortOrder>("updated_desc");
  const { data, isPending, isError, error, refetch } = useIngestionQueueList(page, statusFilter);

  const upload = useUploadIngestionBatch();
  const update = useUpdateIngestionQueueItem();
  const del = useDeleteIngestionQueueItem();
  const requeue = useRequeueIngestionQueueItem();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [parseError, setParseError] = useState<string | null>(null);

  const [editing, setEditing] = useState<IngestionQueueItemSummary | null>(null);
  const [editForm, setEditForm] = useState({ name: "", menu_url: "", nutrition_url: "" });
  const [deleting, setDeleting] = useState<IngestionQueueItemSummary | null>(null);

  const handleFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;

    setParseError(null);
    try {
      const text = await file.text();
      const items = parseBulkJson(text);
      upload.mutate({ items });
    } catch (err) {
      setParseError(err instanceof Error ? err.message : "Could not read that file as JSON.");
    }
  };

  const openEdit = (item: IngestionQueueItemSummary) => {
    setEditing(item);
    setEditForm({
      name: item.name,
      menu_url: item.menu_url,
      nutrition_url: item.nutrition_url,
    });
  };

  const submitEdit = () => {
    if (!editing) return;
    update.mutate(
      {
        id: editing.id,
        payload: {
          name: editForm.name,
          menu_url: editForm.menu_url,
          nutrition_url: editForm.nutrition_url,
        },
      },
      { onSuccess: () => setEditing(null) },
    );
  };

  const confirmDelete = () => {
    if (!deleting) return;
    del.mutate(deleting.id, { onSuccess: () => setDeleting(null) });
  };

  const items = [...(data?.items ?? [])].sort((a, b) =>
    sortOrder === "name_asc"
      ? a.name.localeCompare(b.name)
      : new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
  );

  const selectFilter = (value: IngestionQueueStatus | undefined) => {
    setStatusFilter(value);
    setPage(1);
  };

  return (
    <div className="container py-8">
      <h1 className="text-xl font-bold tracking-tight text-ink">Ingestion</h1>
      <p className="mt-1 text-sm text-ink-faint">
        Upload a verified batch of restaurants with known menu and nutrition page URLs — Source
        Authority search is skipped, but the menu URL is still validated before the pipeline runs.
        Restaurants are processed strictly one at a time.
      </p>

      <Card className="mt-6 max-w-lg">
        <CardHeader>
          <CardTitle>Upload a batch</CardTitle>
          <CardDescription>
            A JSON file containing an array of {"{ name, menu_url, nutrition_url }"}.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {upload.isSuccess && (
            <Alert>
              <AlertTitle>Batch uploaded</AlertTitle>
              <AlertDescription>
                Queued {upload.data.queued_count} restaurant(s).
                {upload.data.skipped_duplicate_names.length > 0 && (
                  <>
                    {" "}
                    Skipped {upload.data.skipped_duplicate_names.length} duplicate name(s):{" "}
                    {upload.data.skipped_duplicate_names.join(", ")}.
                  </>
                )}
              </AlertDescription>
            </Alert>
          )}
          {(parseError || upload.isError) && (
            <Alert variant="destructive">
              <AlertDescription>
                {parseError ?? (isApiError(upload.error) ? upload.error.message : "Upload failed.")}
              </AlertDescription>
            </Alert>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={handleFileSelected}
          />
          <Button onClick={() => fileInputRef.current?.click()} disabled={upload.isPending}>
            {upload.isPending ? "Uploading…" : "Choose JSON file"}
          </Button>
        </CardContent>
      </Card>

      <div className="mt-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-ink">Queue</h2>
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex flex-wrap gap-1.5">
              {STATUS_FILTERS.map((filter) => (
                <Button
                  key={filter.label}
                  variant={statusFilter === filter.value ? "default" : "outline"}
                  size="sm"
                  onClick={() => selectFilter(filter.value)}
                >
                  {filter.label}
                </Button>
              ))}
            </div>
            <div className="flex items-center gap-1.5">
              <Label htmlFor="sort-order" className="text-[12.5px] text-ink-faint">
                Sort
              </Label>
              <select
                id="sort-order"
                className="h-8 rounded-md border border-line bg-transparent px-2 text-[12.5px] text-ink"
                value={sortOrder}
                onChange={(e) => setSortOrder(e.target.value as SortOrder)}
              >
                <option value="updated_desc">Recently updated</option>
                <option value="name_asc">Name (A–Z)</option>
              </select>
            </div>
          </div>
        </div>
        <div className="mt-3">
          {isPending ? (
            <TableSkeleton />
          ) : isError ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : items.length === 0 ? (
            <p className="text-[12.5px] text-ink-faint">No restaurants queued yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Menu URL</TableHead>
                  <TableHead>Nutrition URL</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => {
                  const badge = STATUS_BADGE[item.status] ?? { label: item.status, variant: "default" as const };
                  const editable = item.status === "queued";
                  const requeueable = item.status === "failed";
                  return (
                    <TableRow key={item.id}>
                      <TableCell className="font-medium">{item.name}</TableCell>
                      <TableCell className="max-w-xs truncate text-ink-faint">{item.menu_url}</TableCell>
                      <TableCell className="max-w-xs truncate text-ink-faint">{item.nutrition_url}</TableCell>
                      <TableCell>
                        <Badge variant={badge.variant}>{badge.label}</Badge>
                        {item.status === "failed" && item.error_message && (
                          <p className="mt-1 max-w-xs text-[11px] text-attn">{item.error_message}</p>
                        )}
                      </TableCell>
                      <TableCell className="text-ink-faint">
                        {new Date(item.updated_at).toLocaleString()}
                      </TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-2">
                          {requeueable && (
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={requeue.isPending}
                              onClick={() => requeue.mutate(item.id)}
                            >
                              Requeue
                            </Button>
                          )}
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={!editable}
                            onClick={() => openEdit(item)}
                          >
                            Edit
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            disabled={!editable}
                            onClick={() => setDeleting(item)}
                          >
                            Delete
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </div>

        {data && data.total > data.page_size && (
          <div className="mt-3 flex items-center justify-end gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page * data.page_size >= data.total}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </div>

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit queued restaurant</DialogTitle>
            <DialogDescription>
              Only available while this row is still queued.
            </DialogDescription>
          </DialogHeader>

          {update.isError && (
            <Alert variant="destructive" className="mb-3">
              <AlertDescription>
                {isApiError(update.error) ? update.error.message : "Update failed."}
              </AlertDescription>
            </Alert>
          )}

          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="edit-name">Restaurant name</Label>
              <Input
                id="edit-name"
                value={editForm.name}
                onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="edit-menu-url">Menu URL</Label>
              <Input
                id="edit-menu-url"
                value={editForm.menu_url}
                onChange={(e) => setEditForm((f) => ({ ...f, menu_url: e.target.value }))}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="edit-nutrition-url">Nutrition URL</Label>
              <Input
                id="edit-nutrition-url"
                value={editForm.nutrition_url}
                onChange={(e) => setEditForm((f) => ({ ...f, nutrition_url: e.target.value }))}
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button onClick={submitEdit} disabled={update.isPending}>
              {update.isPending ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete queued restaurant?</DialogTitle>
            <DialogDescription>
              {deleting?.name} will be removed from the queue. This can&apos;t be undone.
            </DialogDescription>
          </DialogHeader>

          {del.isError && (
            <Alert variant="destructive" className="mb-3">
              <AlertDescription>
                {isApiError(del.error) ? del.error.message : "Delete failed."}
              </AlertDescription>
            </Alert>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmDelete} disabled={del.isPending}>
              {del.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
