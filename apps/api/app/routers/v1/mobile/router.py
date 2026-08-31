"""Mobile-facing API surface.

Consumed by the end-user mobile app: read-mostly restaurant/menu data plus
whatever account endpoints a consumer app needs. No business logic yet —
this establishes the module boundary and mount point.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/mobile", tags=["mobile"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"module": "mobile", "status": "ok"}
