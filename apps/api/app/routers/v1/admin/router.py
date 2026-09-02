"""Admin-facing API surface.

Consumed by the Next.js admin dashboard: restaurant listing, review
queue, ingestion triggering, users, audit log, etc.
"""

import uuid

from fastapi import APIRouter

from apps.api.app.dependencies.audit import AuditServiceDep
from apps.api.app.dependencies.auth import CurrentUserDep, require_permission
from apps.api.app.dependencies.db import DbSessionDep
from apps.api.app.dependencies.pagination import PaginationDep
from apps.api.app.dependencies.review import ReviewServiceDep
from core.config.exceptions import NotFoundError
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.audit_log import AuditLogEntry
from core.schemas.auth import Permission
from core.schemas.common import PaginatedResponse
from core.schemas.ingestion import IngestionTriggerRequest, IngestionTriggerResult
from core.schemas.restaurant import Restaurant, RestaurantSummary
from core.schemas.review import (
    ReviewActionResult,
    ReviewDecisionRequest,
    ReviewDetail,
    ReviewEditRequest,
    ReviewSummary,
)
from core.schemas.user import UserPublic
from database.repositories.audit_log_repository import AuditLogRepository
from database.repositories.restaurant_repository import RestaurantRepository

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"module": "admin", "status": "ok"}


# --- VIEWER tier: read-only access ---
@router.get(
    "/restaurants",
    response_model=PaginatedResponse[RestaurantSummary],
    dependencies=[require_permission(Permission.RESTAURANT_READ)],
)
async def list_restaurants(db: DbSessionDep, pagination: PaginationDep) -> PaginatedResponse[RestaurantSummary]:
    summaries, total = await RestaurantRepository(db).list_paginated(
        page=pagination.page, page_size=pagination.page_size
    )
    return PaginatedResponse(items=summaries, page=pagination.page, page_size=pagination.page_size, total=total)


@router.get(
    "/restaurants/{restaurant_id}",
    response_model=Restaurant,
    dependencies=[require_permission(Permission.RESTAURANT_READ)],
)
async def get_restaurant(restaurant_id: uuid.UUID, db: DbSessionDep) -> Restaurant:
    restaurant = await RestaurantRepository(db).get_full_tree(restaurant_id)
    if restaurant is None:
        raise NotFoundError(f"No restaurant with id {restaurant_id}")
    return restaurant


# --- REVIEWER tier: human-in-the-loop review queue for the collector
# workflow's paused runs (workflows/collector_workflow/nodes/
# human_review.py). Every action here resumes the paused LangGraph run
# for that ProposedChange's thread_id via ReviewService — see that
# module for how approve/reject/edit_then_approve map onto the graph's
# interrupt/resume semantics, and for why every decision is audited
# before/alongside the resume itself. ---


@router.get(
    "/reviews",
    response_model=list[ReviewSummary],
    dependencies=[require_permission(Permission.REVIEW_READ)],
)
async def list_pending_reviews(review_service: ReviewServiceDep, limit: int = 100) -> list[ReviewSummary]:
    records = await review_service.list_pending(limit=limit)
    return [ReviewSummary.model_validate(record) for record in records]


@router.get(
    "/reviews/{proposed_change_id}",
    response_model=ReviewDetail,
    dependencies=[require_permission(Permission.REVIEW_READ)],
)
async def get_review_detail(proposed_change_id: uuid.UUID, review_service: ReviewServiceDep) -> ReviewDetail:
    record = await review_service.get_detail(proposed_change_id)
    return ReviewDetail.model_validate(record)


@router.post(
    "/reviews/{proposed_change_id}/approve",
    response_model=ReviewActionResult,
    dependencies=[require_permission(Permission.REVIEW_WRITE)],
)
async def approve_review(
    proposed_change_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    user: CurrentUserDep,
    db: DbSessionDep,
    review_service: ReviewServiceDep,
) -> ReviewActionResult:
    outcome = await review_service.approve(proposed_change_id, reviewer=user, reason=payload.reason)
    await db.commit()
    return ReviewActionResult(
        proposed_change_id=outcome.proposed_change.id,
        status=outcome.proposed_change.status,
        published_restaurant_id=(
            uuid.UUID(outcome.published_restaurant_id) if outcome.published_restaurant_id else None
        ),
        errors=outcome.errors,
    )


@router.post(
    "/reviews/{proposed_change_id}/reject",
    response_model=ReviewActionResult,
    dependencies=[require_permission(Permission.REVIEW_WRITE)],
)
async def reject_review(
    proposed_change_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    user: CurrentUserDep,
    db: DbSessionDep,
    review_service: ReviewServiceDep,
) -> ReviewActionResult:
    outcome = await review_service.reject(proposed_change_id, reviewer=user, reason=payload.reason)
    await db.commit()
    return ReviewActionResult(
        proposed_change_id=outcome.proposed_change.id,
        status=outcome.proposed_change.status,
        errors=outcome.errors,
    )


@router.post(
    "/reviews/{proposed_change_id}/edit-approve",
    response_model=ReviewActionResult,
    dependencies=[require_permission(Permission.REVIEW_WRITE)],
)
async def edit_then_approve_review(
    proposed_change_id: uuid.UUID,
    payload: ReviewEditRequest,
    user: CurrentUserDep,
    db: DbSessionDep,
    review_service: ReviewServiceDep,
) -> ReviewActionResult:
    outcome = await review_service.edit_then_approve(
        proposed_change_id,
        reviewer=user,
        edited_structured_json=payload.edited_structured_json,
        reason=payload.reason,
    )
    await db.commit()
    return ReviewActionResult(
        proposed_change_id=outcome.proposed_change.id,
        status=outcome.proposed_change.status,
        published_restaurant_id=(
            uuid.UUID(outcome.published_restaurant_id) if outcome.published_restaurant_id else None
        ),
        errors=outcome.errors,
    )


# --- DATA_MANAGER tier: can trigger ingestion. Restaurant rows
# themselves are never created directly through this API — the only
# write path into the restaurants table is
# workflows/collector_workflow/nodes/publish.py, reachable exclusively
# through an approved review (see /reviews/{id}/approve below). ---
@router.post(
    "/ingestion/trigger",
    response_model=IngestionTriggerResult,
    dependencies=[require_permission(Permission.INGESTION_TRIGGER)],
)
async def trigger_ingestion(
    payload: IngestionTriggerRequest, user: CurrentUserDep, db: DbSessionDep, audit: AuditServiceDep
) -> IngestionTriggerResult:
    """Enqueues apps.worker.app.jobs.restaurant_ingestion's RQ job by
    dotted import-path string rather than importing the callable directly
    — the api process's image never includes apps/worker (see
    apps/api/Dockerfile's COPY list), only the worker process's does, and
    RQ only needs to resolve that path once a worker actually dequeues
    the job, not at enqueue time. This endpoint's only responsibility is
    the enqueue + audit record; Source Authority resolution, crawling,
    and the collector workflow itself all happen in the worker process
    (see apps/worker/README.md). A fresh restaurant_seed_id is minted per
    trigger request — the dedup lock the job itself acquires
    (infrastructure/queue/lock.py) is what prevents two concurrent
    ingestions from actually racing, not this endpoint."""
    from fastapi.concurrency import run_in_threadpool

    from infrastructure.queue.queues import QUEUE_RESTAURANT_INGESTION, get_queue

    restaurant_seed_id = str(uuid.uuid4())

    def _enqueue() -> str:
        queue = get_queue(QUEUE_RESTAURANT_INGESTION)
        job = queue.enqueue(
            "apps.worker.app.jobs.restaurant_ingestion.run_restaurant_ingestion",
            restaurant_seed_id=restaurant_seed_id,
            name=payload.name,
            city=payload.city,
            state=payload.state,
            country=payload.country,
            phone=payload.phone,
        )
        return job.id

    job_id = await run_in_threadpool(_enqueue)

    await audit.log(
        action=AuditAction.AGENT_RUN_TRIGGER,
        entity_type=AuditEntityType.AGENT_RUN,
        entity_id=restaurant_seed_id,
        actor=user,
        metadata={"job_id": job_id, "restaurant_name": payload.name},
    )
    await db.commit()
    return IngestionTriggerResult(job_id=job_id, restaurant_seed_id=restaurant_seed_id)


# --- read-only tier available to any role holding AUDIT_LOG_READ ---
@router.get(
    "/audit-log",
    response_model=list[AuditLogEntry],
    dependencies=[require_permission(Permission.AUDIT_LOG_READ)],
)
async def list_audit_log(db: DbSessionDep, limit: int = 100) -> list[AuditLogEntry]:
    entries = await AuditLogRepository(db).list_recent(limit=limit)
    return [AuditLogEntry.model_validate(entry) for entry in entries]


# --- SUPER_ADMIN tier: user management ---
@router.get(
    "/users",
    response_model=list[UserPublic],
    dependencies=[require_permission(Permission.USER_READ)],
)
async def list_users_placeholder() -> list[UserPublic]:
    return []
