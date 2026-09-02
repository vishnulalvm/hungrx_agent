import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost } from "@/lib/api/client";
import { queryKeys } from "@/hooks/query-keys";
import type {
  ReviewActionResult,
  ReviewDecisionRequest,
  ReviewDetail,
  ReviewEditRequest,
  ReviewSummary,
} from "@/lib/api/types";

export function usePendingReviews(limit = 100) {
  return useQuery({
    queryKey: queryKeys.reviews.pending(),
    queryFn: () => apiGet<ReviewSummary[]>("admin/reviews", { limit }),
  });
}

export function useReviewDetail(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.reviews.detail(id ?? ""),
    queryFn: () => apiGet<ReviewDetail>(`admin/reviews/${id}`),
    enabled: id !== undefined,
  });
}

// Approve/reject/edit-approve all resume a paused LangGraph run and, for
// approve/edit-approve, write to production tables server-side
// (workflows/*/nodes/publish.py) — genuinely not safe to reflect before
// the server confirms. Once it does, we optimistically patch the
// already-fetched pending-list cache (drop the row) instead of waiting
// for a full refetch, since that patch only ever reflects a decision
// the server already made — see the module docstring pattern established
// for this endpoint's write semantics in apps/api/app/services/review_service.py.
function removeFromPendingList(
  queryClient: ReturnType<typeof useQueryClient>,
  proposedChangeId: string,
) {
  queryClient.setQueryData<ReviewSummary[]>(queryKeys.reviews.pending(), (old) =>
    old?.filter((review) => review.id !== proposedChangeId),
  );
}

export function useApproveReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ReviewDecisionRequest }) =>
      apiPost<ReviewActionResult>(`admin/reviews/${id}/approve`, payload),
    onSuccess: (_result, { id }) => {
      removeFromPendingList(queryClient, id);
      void queryClient.invalidateQueries({ queryKey: queryKeys.reviews.detail(id) });
      void queryClient.invalidateQueries({ queryKey: ["restaurants"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useRejectReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ReviewDecisionRequest }) =>
      apiPost<ReviewActionResult>(`admin/reviews/${id}/reject`, payload),
    onSuccess: (_result, { id }) => {
      removeFromPendingList(queryClient, id);
      void queryClient.invalidateQueries({ queryKey: queryKeys.reviews.detail(id) });
      void queryClient.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useEditThenApproveReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ReviewEditRequest }) =>
      apiPost<ReviewActionResult>(`admin/reviews/${id}/edit-approve`, payload),
    onSuccess: (_result, { id }) => {
      removeFromPendingList(queryClient, id);
      void queryClient.invalidateQueries({ queryKey: queryKeys.reviews.detail(id) });
      void queryClient.invalidateQueries({ queryKey: ["restaurants"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}
