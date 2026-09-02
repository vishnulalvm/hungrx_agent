"""Authentication API surface: login, token refresh, logout, current user.

Shared by both the admin dashboard and the mobile app — one auth module,
one token format, so a client only ever needs one login integration
regardless of which other /api/v1/* modules it talks to afterwards.
"""

from fastapi import APIRouter, status

from apps.api.app.core.rate_limit import RateLimitLoginDep, RateLimitRefreshDep
from apps.api.app.dependencies.audit import AuditServiceDep
from apps.api.app.dependencies.auth import CurrentUserDep
from apps.api.app.dependencies.db import DbSessionDep
from apps.api.app.dependencies.settings import SettingsDep
from apps.api.app.services.auth_service import AuthService
from core.config.exceptions import UnauthorizedError
from core.schemas.audit import AuditAction
from core.schemas.user import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserPublic,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"module": "auth", "status": "ok"}


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: DbSessionDep,
    settings: SettingsDep,
    audit: AuditServiceDep,
    _rate_limit: RateLimitLoginDep,
) -> TokenResponse:
    service = AuthService(db, settings)
    try:
        user = await service.authenticate(email=payload.email, password=payload.password)
    except UnauthorizedError:
        await audit.log_security_event(
            action=AuditAction.LOGIN_FAILURE, actor_email=payload.email.lower()
        )
        await db.commit()
        raise

    tokens = await service.issue_tokens(user)
    await audit.log_security_event(action=AuditAction.LOGIN_SUCCESS, actor=user)
    await db.commit()
    return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    db: DbSessionDep,
    settings: SettingsDep,
    audit: AuditServiceDep,
    _rate_limit: RateLimitRefreshDep,
) -> TokenResponse:
    service = AuthService(db, settings)
    tokens = await service.refresh(raw_refresh_token=payload.refresh_token)
    await audit.log_security_event(action=AuditAction.TOKEN_REFRESH, actor=tokens.user)
    await db.commit()
    return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest, db: DbSessionDep, settings: SettingsDep, audit: AuditServiceDep
) -> None:
    service = AuthService(db, settings)
    user = await service.logout(payload.refresh_token)
    await audit.log_security_event(action=AuditAction.LOGOUT, actor=user)
    await db.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    user: CurrentUserDep, db: DbSessionDep, settings: SettingsDep, audit: AuditServiceDep
) -> None:
    """Revokes every refresh token for the current user — "sign out of all
    devices." Requires a valid access token, unlike /logout which only
    needs the refresh token being revoked."""
    service = AuthService(db, settings)
    await service.logout_all_sessions(user.id)
    await audit.log_security_event(action=AuditAction.LOGOUT_ALL, actor=user)
    await db.commit()


@router.get("/me", response_model=UserPublic)
async def me(user: CurrentUserDep) -> UserPublic:
    return UserPublic.model_validate(user)
