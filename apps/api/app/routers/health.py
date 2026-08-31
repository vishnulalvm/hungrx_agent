"""Unversioned health/readiness endpoints for container orchestration and
uptime checks. Deliberately outside /api/v1 — infra probes shouldn't be
coupled to API versioning."""

import logging

from fastapi import APIRouter
from sqlalchemy import text

from apps.api.app.dependencies.db import DbSessionDep

router = APIRouter(tags=["health"])

logger = logging.getLogger("hungrx.health")

SERVICE_NAME = "hungrx-api"
SERVICE_VERSION = "0.1.0"


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness — process is up and can serve requests. Must never touch
    the database/Redis, so it can't false-negative on a dependency blip."""
    return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}


@router.get("/health/ready")
async def readiness(db: DbSessionDep) -> dict[str, str]:
    """Readiness — process is up AND its dependencies (database) are
    reachable. Used by orchestrators to gate traffic, not just restarts."""
    await db.execute(text("SELECT 1"))
    return {"status": "ready", "service": SERVICE_NAME}
