"""Authentication API surface: login, token refresh, logout.

Shared by both the admin dashboard and the mobile app — one auth module,
one token format, so a client only ever needs one login integration
regardless of which other /api/v1/* modules it talks to afterwards. No
user model / credential verification yet — that lands with user business
logic; this establishes the module boundary and mount point.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"module": "auth", "status": "ok"}
