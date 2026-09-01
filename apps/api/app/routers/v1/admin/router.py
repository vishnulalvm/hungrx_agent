"""Admin-facing API surface.

Consumed by the Next.js admin dashboard: restaurant CRUD, review queue,
ingestion controls, users, audit log, etc. No restaurant business logic
yet — the endpoints below exist only to demonstrate/exercise each
permission tier (SUPER_ADMIN / DATA_MANAGER / REVIEWER / VIEWER) via
`require_permission`, ahead of the real handlers landing on these same
routes.
"""

import uuid

from fastapi import APIRouter

from apps.api.app.dependencies.audit import AuditServiceDep
from apps.api.app.dependencies.auth import CurrentUserDep, require_permission
from apps.api.app.dependencies.db import DbSessionDep
from apps.api.app.dependencies.review import ReviewServiceDep
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.audit_log import AuditLogEntry
from core.schemas.auth import Permission
from core.schemas.review import (
    ReviewActionResult,
    ReviewDecisionRequest,
    ReviewDetail,
    ReviewEditRequest,
    ReviewSummary,
)
from core.schemas.user import UserPublic
from database.repositories.audit_log_repository import AuditLogRepository

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"module": "admin", "status": "ok"}


# --- VIEWER tier: read-only access ---
@router.get("/restaurants", dependencies=[require_permission(Permission.RESTAURANT_READ)])
async def list_restaurants_placeholder() -> dict[str, str]:
    return {"detail": "restaurant listing not yet implemented"}


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


# --- DATA_MANAGER tier: can write restaurant data and trigger ingestion ---
@router.post("/restaurants", dependencies=[require_permission(Permission.RESTAURANT_WRITE)])
async def create_restaurant_placeholder(
    user: CurrentUserDep, db: DbSessionDep, audit: AuditServiceDep
) -> dict[str, str]:
    entry = await audit.log(
        action=AuditAction.RESTAURANT_CREATE,
        entity_type=AuditEntityType.RESTAURANT,
        entity_id="placeholder",
        actor=user,
    )
    await db.commit()
    return {"detail": "restaurant creation not yet implemented", "audit_log_id": str(entry.id)}


@router.post("/ingestion/trigger", dependencies=[require_permission(Permission.INGESTION_TRIGGER)])
async def trigger_ingestion_placeholder(
    user: CurrentUserDep, db: DbSessionDep, audit: AuditServiceDep
) -> dict[str, str]:
    await audit.log(
        action=AuditAction.AGENT_RUN_TRIGGER,
        entity_type=AuditEntityType.AGENT_RUN,
        entity_id="placeholder",
        actor=user,
    )
    await db.commit()
    return {"detail": "ingestion trigger not yet implemented"}


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
