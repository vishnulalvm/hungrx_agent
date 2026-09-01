"""Admin-facing API surface.

Consumed by the Next.js admin dashboard: restaurant CRUD, review queue,
ingestion controls, users, audit log, etc. No restaurant business logic
yet — the endpoints below exist only to demonstrate/exercise each
permission tier (SUPER_ADMIN / DATA_MANAGER / REVIEWER / VIEWER) via
`require_permission`, ahead of the real handlers landing on these same
routes.
"""

from fastapi import APIRouter

from apps.api.app.dependencies.audit import AuditServiceDep
from apps.api.app.dependencies.auth import CurrentUserDep, require_permission
from apps.api.app.dependencies.db import DbSessionDep
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.audit_log import AuditLogEntry
from core.schemas.auth import Permission
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


# --- REVIEWER tier: can act on the review queue ---
@router.post("/review/{item_id}/confirm", dependencies=[require_permission(Permission.REVIEW_WRITE)])
async def confirm_review_item_placeholder(
    item_id: str, user: CurrentUserDep, db: DbSessionDep, audit: AuditServiceDep
) -> dict[str, str]:
    # Demonstrates the pattern every future mutating endpoint should
    # follow: perform the write, then log it in the same transaction so
    # both commit or roll back together.
    await audit.log(
        action=AuditAction.PROPOSED_CHANGE_APPROVE,
        entity_type=AuditEntityType.PROPOSED_CHANGE,
        entity_id=item_id,
        actor=user,
    )
    await db.commit()
    return {"detail": f"confirming review item {item_id} not yet implemented"}


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
