"""LangGraph agent API surface: inspect collector and reviewer workflow
run status (agent-runs page in the admin dashboard).

Read-only — triggering a run happens indirectly, via
/admin/ingestion/trigger (which enqueues the RQ job whose graph creates
its own AgentRun) or by a reviewer_workflow job the maintenance-polling
sweep enqueues; nothing here starts a run directly, so there's exactly
one path that creates an AgentRun row (the graph's own
source_authority/temporal_hash_polling node), not one per caller.
"""

import uuid

from fastapi import APIRouter

from apps.api.app.dependencies.auth import require_permission
from apps.api.app.dependencies.db import DbSessionDep
from apps.api.app.dependencies.pagination import PaginationDep
from core.config.exceptions import NotFoundError
from core.schemas.agent_run import AgentRun
from core.schemas.auth import Permission
from core.schemas.common import PaginatedResponse
from database.repositories.agent_run_repository import AgentRunRepository

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"module": "agents", "status": "ok"}


@router.get(
    "/runs",
    response_model=PaginatedResponse[AgentRun],
    dependencies=[require_permission(Permission.AGENT_RUN_READ)],
)
async def list_agent_runs(db: DbSessionDep, pagination: PaginationDep) -> PaginatedResponse[AgentRun]:
    records, total = await AgentRunRepository(db).list_paginated(
        page=pagination.page, page_size=pagination.page_size
    )
    return PaginatedResponse(
        items=[AgentRun.model_validate(record) for record in records],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )


@router.get(
    "/runs/{run_id}",
    response_model=AgentRun,
    dependencies=[require_permission(Permission.AGENT_RUN_READ)],
)
async def get_agent_run(run_id: uuid.UUID, db: DbSessionDep) -> AgentRun:
    record = await AgentRunRepository(db).get_by_id(run_id)
    if record is None:
        raise NotFoundError(f"No AgentRun with id {run_id}")
    return AgentRun.model_validate(record)
