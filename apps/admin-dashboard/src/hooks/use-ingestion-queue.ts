import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api/client";
import { queryKeys } from "@/hooks/query-keys";
import type {
  IngestionQueueBulkUploadRequest,
  IngestionQueueBulkUploadResult,
  IngestionQueueItemEditRequest,
  IngestionQueueItemSummary,
  IngestionQueueStatus,
  PaginatedResponse,
} from "@/lib/api/types";

export function useIngestionQueueList(page: number, status?: IngestionQueueStatus) {
  return useQuery({
    queryKey: queryKeys.ingestionQueue.list(page, status),
    queryFn: () =>
      apiGet<PaginatedResponse<IngestionQueueItemSummary>>("admin/ingestion-queue", {
        page,
        page_size: 20,
        status,
      }),
    refetchInterval: 5000,
  });
}

// Uploading enqueues the dispatcher (server-side) which processes the
// queue strictly one restaurant at a time — nothing here happens
// optimistically beyond the mutation's own pending state, since the
// server's create_batch response is what tells the caller which rows
// were actually queued vs. skipped as in-batch duplicates.
export function useUploadIngestionBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: IngestionQueueBulkUploadRequest) =>
      apiPost<IngestionQueueBulkUploadResult>("admin/ingestion-queue/upload", payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ingestion-queue"] });
    },
  });
}

export function useUpdateIngestionQueueItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: IngestionQueueItemEditRequest }) =>
      apiPatch<IngestionQueueItemSummary>(`admin/ingestion-queue/${id}`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ingestion-queue"] });
    },
  });
}

export function useDeleteIngestionQueueItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiDelete<void>(`admin/ingestion-queue/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ingestion-queue"] });
    },
  });
}

export function useRequeueIngestionQueueItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiPost<IngestionQueueItemSummary>(`admin/ingestion-queue/${id}/requeue`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ingestion-queue"] });
    },
  });
}
