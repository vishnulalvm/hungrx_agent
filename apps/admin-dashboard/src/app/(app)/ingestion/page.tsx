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
import type { IngestionQueueItemInput, IngestionQueueItemSummary } from "@/lib/api/types";
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

function parseBulkJson(raw: string): IngestionQueueItemInput[] {
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error("Expected a JSON array of restaurant rows");
  }
  return parsed;
}

export default function IngestionPage() {
  const [page, setPage] = useState(1);
  const { data, isPending, isError, error, refetch } = useIngestionQueueList(page);

  const upload = useUploadIngestionBatch();
  const update = useUpdateIngestionQueueItem();
  const del = useDeleteIngestionQueueItem();
  const requeue = useRequeueIngestionQueueItem();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [parseError, setParseError] = useState<string | null>(null);

  const [editing, setEditing] = useState<IngestionQueueItemSummary | null>(null);
  const [editForm, setEditForm] = useState({ name: "", official_url: "", city: "", state: "", country: "", phone: "" });
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
      official_url: item.official_url,
      city: item.city ?? "",
      state: item.state ?? "",
      country: item.country ?? "",
      phone: item.phone ?? "",
    });
  };

  const submitEdit = () => {
    if (!editing) return;
    update.mutate(
      {
        id: editing.id,
        payload: {
          name: editForm.name,
          official_url: editForm.official_url,
          city: editForm.city || undefined,
          state: editForm.state || undefined,
          country: editForm.country ? editForm.country.toUpperCase() : undefined,
          phone: editForm.phone || undefined,
        },
      },
      { onSuccess: () => setEditing(null) },
    );
  };

  const confirmDelete = () => {
    if (!deleting) return;
    del.mutate(deleting.id, { onSuccess: () => setDeleting(null) });
  };

  const items = data?.items ?? [];

  return (
    <div className="container py-8">
      <h1 className="text-xl font-bold tracking-tight text-ink">Ingestion</h1>
      <p className="mt-1 text-sm text-ink-faint">
        Upload a verified batch of restaurants with known official URLs — Source Authority search is
        skipped, but each URL is still validated before the pipeline runs. Restaurants are processed
        strictly one at a time.
      </p>

      <Card className="mt-6 max-w-lg">
        <CardHeader>
          <CardTitle>Upload a batch</CardTitle>
          <CardDescription>
            A JSON file containing an array of {"{ name, official_url, city?, state?, country?, phone? }"}.
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
        <h2 className="text-sm font-semibold text-ink">Queue</h2>
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
                  <TableHead>Official URL</TableHead>
                  <TableHead>Location</TableHead>
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
                      <TableCell className="max-w-xs truncate text-ink-faint">{item.official_url}</TableCell>
                      <TableCell className="text-ink-faint">
                        {[item.city, item.state, item.country].filter(Boolean).join(", ") || "—"}
                      </TableCell>
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
              <Label htmlFor="edit-url">Official URL</Label>
              <Input
                id="edit-url"
                value={editForm.official_url}
                onChange={(e) => setEditForm((f) => ({ ...f, official_url: e.target.value }))}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="edit-city">City</Label>
                <Input
                  id="edit-city"
                  value={editForm.city}
                  onChange={(e) => setEditForm((f) => ({ ...f, city: e.target.value }))}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="edit-state">State</Label>
                <Input
                  id="edit-state"
                  value={editForm.state}
                  onChange={(e) => setEditForm((f) => ({ ...f, state: e.target.value }))}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="edit-country">Country code</Label>
                <Input
                  id="edit-country"
                  maxLength={2}
                  value={editForm.country}
                  onChange={(e) => setEditForm((f) => ({ ...f, country: e.target.value }))}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="edit-phone">Phone</Label>
                <Input
                  id="edit-phone"
                  value={editForm.phone}
                  onChange={(e) => setEditForm((f) => ({ ...f, phone: e.target.value }))}
                />
              </div>
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
