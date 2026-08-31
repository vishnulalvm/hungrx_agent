"""Authentication dependency foundation.

No user model or database lookup yet (that arrives with the users/auth
business logic) — this validates the JWT itself and exposes the decoded
claims as an `AuthContext`. Both the mobile and admin routers will depend
on `get_current_auth` (or `require_role(...)`) once real user records
exist; for now it establishes the shape every protected route will use.
"""

from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from apps.api.app.core.security import decode_token
from apps.api.app.dependencies.settings import SettingsDep
from core.config.exceptions import ForbiddenError, UnauthorizedError

_bearer_scheme = HTTPBearer(auto_error=False)


class AuthContext(BaseModel):
    subject: str
    token_type: str
    role: str | None = None


async def get_current_auth(
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> AuthContext:
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

    return AuthContext(
        subject=payload["sub"],
        token_type=payload["type"],
        role=payload.get("role"),
    )


CurrentAuthDep = Annotated[AuthContext, Depends(get_current_auth)]


def require_role(*allowed_roles: str):
    """Route-level dependency factory: raises 403 unless the caller's role
    (embedded in the JWT) is one of `allowed_roles`."""

    async def _check(auth: CurrentAuthDep) -> AuthContext:
        if auth.role not in allowed_roles:
            raise ForbiddenError("You do not have permission to perform this action")
        return auth

    return _check
