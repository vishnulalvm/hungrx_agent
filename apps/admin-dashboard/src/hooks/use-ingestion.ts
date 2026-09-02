import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPost } from "@/lib/api/client";
import type { IngestionTriggerRequest, IngestionTriggerResult } from "@/lib/api/types";

// Triggering ingestion enqueues a real background job
// (apps/worker/app/jobs/restaurant_ingestion.py) — no optimistic UI here
// beyond the mutation's own pending state; the caller only learns the
// job_id once the server confirms the enqueue actually happened, since a
// failed enqueue with an optimistic "queued" row would misrepresent
// whether any work is actually going to happen.
export function useTriggerIngestion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: IngestionTriggerRequest) =>
      apiPost<IngestionTriggerResult>("admin/ingestion/trigger", payload),
    onSuccess: () => {
      // A successful trigger will (once the worker resolves the source
      // and the collector workflow's source_authority node creates its
      // AgentRun) eventually show up in the agent-runs list — refetch so
      // it appears without the user needing to navigate away and back.
      void queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
  });
}
