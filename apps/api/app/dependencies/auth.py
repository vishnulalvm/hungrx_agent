"""Authentication/authorization dependencies.

`get_current_auth` verifies the bearer JWT and loads the corresponding
User row (so a deactivated or deleted account is rejected even with a
still-valid, unexpired token — role/is_active always reflect current DB
state, not what was true when the token was issued). `require_role` and
`require_permission` build route-level guards on top of it.
"""

import uuid
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from apps.api.app.core.security import decode_token
from apps.api.app.dependencies.db import DbSessionDep
from apps.api.app.dependencies.settings import SettingsDep
from core.config.exceptions import ForbiddenError, UnauthorizedError
from core.schemas.auth import Permission, Role, permissions_for_role
from database.models.user import User
from database.repositories.user_repository import UserRepository

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    settings: SettingsDep,
    db: DbSessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    if credentials is None:
        raise UnauthorizedError("Missing bearer token")

    try:
        payload = decode_token(credentials.credentials, settings)
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Token has expired") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid token") from exc

    if payload.get("type") != "access":
        raise UnauthorizedError("Access token required")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("Invalid token subject") from exc

    user = await UserRepository(db).get_by_id(user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Account is no longer active")

    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def require_role(*allowed_roles: Role):
    """Route-level guard: 403s unless the caller's role is one of
    `allowed_roles`. Prefer `require_permission` for most endpoints —
    this is for the rare case an action is tied to a role directly rather
    than a capability (e.g. "only SUPER_ADMIN may promote another admin").

    Returns a `Depends(...)` instance directly, so it drops straight into
    either a route's `dependencies=[...]` list or a parameter default.
    """

    async def _check(user: CurrentUserDep) -> User:
        if user.role not in allowed_roles:
            raise ForbiddenError("You do not have permission to perform this action")
        return user

    return Depends(_check)


def require_permission(permission: Permission):
    """Route-level guard: 403s unless the caller's role grants
    `permission`, per the ROLE_PERMISSIONS matrix in core.schemas.auth.
    This is the primary authorization mechanism — endpoints declare what
    capability they need, not which roles happen to have it today.

    Returns a `Depends(...)` instance directly, so it drops straight into
    either a route's `dependencies=[...]` list or a parameter default.
    """

    async def _check(user: CurrentUserDep) -> User:
        if permission not in permissions_for_role(user.role):
            raise ForbiddenError("You do not have permission to perform this action")
        return user

    return Depends(_check)
