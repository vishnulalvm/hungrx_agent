"""LangGraph agent API surface: trigger/inspect collector and reviewer
workflow runs (agent-runs page in the admin dashboard).

No workflow integration yet — this establishes the module boundary and
mount point.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"module": "agents", "status": "ok"}
