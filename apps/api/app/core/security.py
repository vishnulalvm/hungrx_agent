"""Auth foundation: password hashing and JWT encode/decode helpers.

No login/register endpoints or user model yet — this module just gives the
rest of the app (auth router, auth middleware) a stable, tested surface to
build on once the user model exists.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt
from passlib.context import CryptContext

from core.config.settings import Settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TokenType = Literal["access", "refresh"]


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def create_token(
    *,
    subject: str,
    token_type: TokenType,
    settings: Settings,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expires_delta = (
        timedelta(minutes=settings.jwt_access_token_expire_minutes)
        if token_type == "access"
        else timedelta(minutes=settings.jwt_refresh_token_expire_minutes)
    )

    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.api_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    """Raises jwt.PyJWTError (or a subclass) on an invalid/expired token —
    callers (the auth dependency) translate that into UnauthorizedError."""
    return jwt.decode(token, settings.api_secret_key, algorithms=[settings.jwt_algorithm])
