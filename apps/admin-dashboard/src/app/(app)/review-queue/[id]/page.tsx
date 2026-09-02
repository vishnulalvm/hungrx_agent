"use client";

import { use, useState } from "react";
import { useRouter } from "next/navigation";
import {
  useReviewDetail,
  useApproveReview,
  useRejectReview,
  useEditThenApproveReview,
} from "@/hooks/use-reviews";
import { isApiError } from "@/lib/api/errors";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ErrorState } from "@/components/error-state";

export default function ReviewDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { data: review, isPending, isError, error, refetch } = useReviewDetail(id);
  const approve = useApproveReview();
  const reject = useRejectReview();
  const editThenApprove = useEditThenApproveReview();
  const [reason, setReason] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [editedJson, setEditedJson] = useState("");
  const [editJsonError, setEditJsonError] = useState<string | null>(null);

  const isBusy = approve.isPending || reject.isPending || editThenApprove.isPending;
  const actionError = approve.error ?? reject.error ?? editThenApprove.error;

  const handleApprove = () => {
    approve.mutate(
      { id, payload: { reason: reason || undefined } },
      { onSuccess: () => router.push("/review-queue") },
    );
  };

  const handleReject = () => {
    reject.mutate(
      { id, payload: { reason: reason || undefined } },
      { onSuccess: () => router.push("/review-queue") },
    );
  };

  const handleStartEditing = () => {
    setEditedJson(JSON.stringify(review?.structured_json ?? {}, null, 2));
    setEditJsonError(null);
    setIsEditing(true);
  };

  const handleSaveEditsAndApprove = () => {
    setEditJsonError(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(editedJson);
    } catch {
      setEditJsonError("Not valid JSON — fix the syntax and try again.");
      return;
    }
    editThenApprove.mutate(
      { id, payload: { edited_structured_json: parsed, reason: reason || undefined } },
      { onSuccess: () => router.push("/review-queue") },
    );
  };

  if (isPending) {
    return (
      <div className="container flex flex-col gap-3 py-8">
        <Skeleton className="h-7 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="container py-8">
        <ErrorState error={error} onRetry={() => void refetch()} />
      </div>
    );
  }

  const validation = review.validation_result;
  const isPendingDecision = review.status === "pending";

  return (
    <div className="container py-8">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-bold tracking-tight text-ink">Review {review.entity_type}</h1>
        <Badge variant="accent">{review.status}</Badge>
      </div>
      <p className="mt-1 text-sm text-ink-faint">
        Submitted {new Date(review.created_at).toLocaleString()}
      </p>

      {validation && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-[13px]">
              Validation
              <Badge variant={validation.is_valid ? "ok" : "attn"}>
                {validation.is_valid ? "Valid" : `${validation.issues.length} issue(s)`}
              </Badge>
            </CardTitle>
          </CardHeader>
          {validation.issues.length > 0 && (
            <CardContent className="flex flex-col gap-2">
              {validation.issues.map((issue, index) => (
                <div key={index} className="bg-bg-soft rounded-md p-2 text-[12px]">
                  <span className="font-mono text-ink-faint">{issue.field_path}</span>
                  <span
                    className={issue.severity === "error" ? "ml-2 text-attn" : "ml-2 text-ink-soft"}
                  >
                    {issue.message}
                  </span>
                </div>
              ))}
            </CardContent>
          )}
        </Card>
      )}

      <Card className="mt-4">
        <CardHeader>
          <CardTitle className="text-[13px]">Proposed data</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="bg-bg-soft max-h-96 overflow-auto rounded-md p-3 text-[11.5px] text-ink-soft">
            {JSON.stringify(review.structured_json, null, 2)}
          </pre>
        </CardContent>
      </Card>

      {isPendingDecision && (
        <Card className="mt-4">
          <CardHeader>
            <CardTitle className="text-[13px]">Decision</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {actionError && (
              <Alert variant="destructive">
                <AlertDescription>
                  {isApiError(actionError)
                    ? actionError.message
                    : "The decision could not be recorded."}
                </AlertDescription>
              </Alert>
            )}
            <Textarea
              placeholder="Reason (optional)"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              disabled={isBusy}
            />
            <div className="flex gap-2">
              <Button onClick={handleApprove} disabled={isBusy}>
                {approve.isPending ? "Approving…" : "Approve"}
              </Button>
              <Button variant="destructive" onClick={handleReject} disabled={isBusy}>
                {reject.isPending ? "Rejecting…" : "Reject"}
              </Button>
              {!isEditing && (
                <Button variant="outline" onClick={handleStartEditing} disabled={isBusy}>
                  Edit JSON
                </Button>
              )}
            </div>

            {isEditing && (
              <div className="border-line-strong flex flex-col gap-2 border-t pt-3">
                <p className="text-[12px] text-ink-faint">
                  Edit the proposed data directly, then save — this approves the edited version
                  instead of the original.
                </p>
                {editJsonError && (
                  <Alert variant="destructive">
                    <AlertDescription>{editJsonError}</AlertDescription>
                  </Alert>
                )}
                <Textarea
                  className="min-h-64 font-mono text-[11.5px]"
                  value={editedJson}
                  onChange={(event) => setEditedJson(event.target.value)}
                  disabled={isBusy}
                />
                <div className="flex gap-2">
                  <Button onClick={handleSaveEditsAndApprove} disabled={isBusy}>
                    {editThenApprove.isPending ? "Saving…" : "Save edits & approve"}
                  </Button>
                  <Button variant="outline" onClick={() => setIsEditing(false)} disabled={isBusy}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
