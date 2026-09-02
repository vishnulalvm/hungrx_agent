"""API request/response schemas for triggering restaurant ingestion
(apps/api/app/routers/v1/admin/router.py's /ingestion/trigger). The
actual work happens in apps.worker.app.jobs.restaurant_ingestion — this
endpoint's only job is to enqueue that RQ job and hand back a job id the
caller can poll."""

from pydantic import BaseModel, ConfigDict, Field


class IngestionTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    city: str | None = None
    state: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    phone: str | None = None


class IngestionTriggerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    restaurant_seed_id: str
