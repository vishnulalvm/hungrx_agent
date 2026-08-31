"""Admin-facing API surface.

Consumed by the Next.js admin dashboard: restaurant CRUD, review queue,
ingestion controls, users, audit log, etc. No business logic yet — this
establishes the module boundary and mount point.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"module": "admin", "status": "ok"}
