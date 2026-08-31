from fastapi import APIRouter

from apps.api.app.routers.v1.admin.router import router as admin_router
from apps.api.app.routers.v1.agents.router import router as agents_router
from apps.api.app.routers.v1.auth.router import router as auth_router
from apps.api.app.routers.v1.mobile.router import router as mobile_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(auth_router)
v1_router.include_router(mobile_router)
v1_router.include_router(admin_router)
v1_router.include_router(agents_router)
