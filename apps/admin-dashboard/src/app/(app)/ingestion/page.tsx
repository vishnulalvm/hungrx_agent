"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTriggerIngestion } from "@/hooks/use-ingestion";
import { isApiError } from "@/lib/api/errors";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

const ingestionSchema = z.object({
  name: z.string().min(1, "Restaurant name is required").max(255),
  city: z.string().max(120).optional().or(z.literal("")),
  state: z.string().max(120).optional().or(z.literal("")),
  country: z
    .string()
    .max(2)
    .optional()
    .or(z.literal(""))
    .refine((value) => !value || value.length === 2, "Use a 2-letter country code, e.g. US"),
  phone: z.string().max(50).optional().or(z.literal("")),
});

type IngestionFormValues = z.infer<typeof ingestionSchema>;

export default function IngestionPage() {
  const trigger = useTriggerIngestion();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<IngestionFormValues>({ resolver: zodResolver(ingestionSchema) });

  const onSubmit = (values: IngestionFormValues) => {
    trigger.mutate(
      {
        name: values.name,
        city: values.city || undefined,
        state: values.state || undefined,
        country: values.country ? values.country.toUpperCase() : undefined,
        phone: values.phone || undefined,
      },
      { onSuccess: () => reset() },
    );
  };

  return (
    <div className="container py-8">
      <h1 className="text-xl font-bold tracking-tight text-ink">Ingestion</h1>
      <p className="mt-1 text-sm text-ink-faint">
        Trigger Source Authority resolution and, once verified, the full collector pipeline for a
        new restaurant.
      </p>

      <Card className="mt-6 max-w-lg">
        <CardHeader>
          <CardTitle>Ingest a restaurant</CardTitle>
          <CardDescription>
            Enough to identify the restaurant — the pipeline finds the rest.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={handleSubmit(onSubmit)}>
            {trigger.isSuccess && (
              <Alert>
                <AlertTitle>Ingestion queued</AlertTitle>
                <AlertDescription>
                  Job <code className="font-mono">{trigger.data.job_id}</code> was enqueued. Track
                  its progress on the Agent Runs page.
                </AlertDescription>
              </Alert>
            )}
            {trigger.isError && (
              <Alert variant="destructive">
                <AlertDescription>
                  {isApiError(trigger.error) ? trigger.error.message : "Failed to queue ingestion."}
                </AlertDescription>
              </Alert>
            )}

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="name">Restaurant name</Label>
              <Input id="name" {...register("name")} />
              {errors.name && <p className="text-[11.5px] text-attn">{errors.name.message}</p>}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="city">City</Label>
                <Input id="city" {...register("city")} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="state">State</Label>
                <Input id="state" {...register("state")} />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="country">Country code</Label>
                <Input id="country" placeholder="US" maxLength={2} {...register("country")} />
                {errors.country && (
                  <p className="text-[11.5px] text-attn">{errors.country.message}</p>
                )}
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="phone">Phone</Label>
                <Input id="phone" {...register("phone")} />
              </div>
            </div>

            <Button type="submit" disabled={trigger.isPending} className="mt-1">
              {trigger.isPending ? "Queuing…" : "Trigger ingestion"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
