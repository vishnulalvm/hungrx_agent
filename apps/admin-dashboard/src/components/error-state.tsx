import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { isApiError } from "@/lib/api/errors";

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = isApiError(error) ? error.message : "Something went wrong. Please try again.";

  return (
    <Alert variant="destructive">
      <AlertTitle>Couldn&apos;t load this</AlertTitle>
      <AlertDescription className="flex items-center justify-between gap-3">
        <span>{message}</span>
        {onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}
