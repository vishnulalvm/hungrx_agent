"""Password hashing, JWT issuance/verification, and refresh-token hashing.

Production practices in play:
  - passwords hashed with bcrypt (via the `bcrypt` library directly —
    passlib's bcrypt backend detection is broken against bcrypt>=4.1,
    which dropped the `__about__` attribute passlib probes for; calling
    bcrypt directly sidesteps that unmaintained compatibility shim
    entirely), never stored/logged in plaintext, never echoed back in any
    response schema.
  - short-lived access tokens (default 60 min) carry the user's role so
    authorization checks don't need a DB round trip on every request.
  - refresh tokens are long-lived but tracked server-side by the SHA-256
    hash of the token (never the raw token) in the refresh_tokens table,
    so logout / "revoke all sessions" can actually invalidate them before
    natural expiry — a capability pure stateless JWTs don't have.
  - refresh tokens carry a random `jti` (not derived from anything
    guessable) that IS the value that gets hashed and looked up, so a
    leaked access token can never be replayed as a refresh token and vice
    versa (enforced by the `type` claim too, belt-and-suspenders).
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from core.config.settings import Settings

TokenType = Literal["access", "refresh"]

# bcrypt's underlying algorithm silently truncates at 72 bytes; rejecting
# longer input up front means a very long passphrase never gets silently
# weakened (its extra characters ignored) without the caller knowing.
_MAX_PASSWORD_BYTES = 72


def hash_password(plain_password: str) -> str:
    encoded = plain_password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {_MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    encoded = plain_password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(encoded, hashed_password.encode("utf-8"))


def hash_token(raw_token: str) -> str:
    """One-way hash used to store/look up refresh tokens without ever
    persisting the bearer-usable value itself."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _token_expiry(token_type: TokenType, settings: Settings) -> timedelta:
    return (
        timedelta(minutes=settings.jwt_access_token_expire_minutes)
        if token_type == "access"
        else timedelta(minutes=settings.jwt_refresh_token_expire_minutes)
    )


def create_access_token(*, subject: str, role: str, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "access",
        "role": role,
        # A random jti guarantees two access tokens for the same user are
        # never byte-identical, even when minted within the same second
        # (iat/exp only carry second precision) — e.g. immediately after a
        # refresh. Not tracked server-side like the refresh token's jti;
        # access tokens stay stateless by design.
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + _token_expiry("access", settings),
    }
    return jwt.encode(payload, settings.api_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(*, subject: str, settings: Settings) -> tuple[str, str, datetime]:
    """Returns (raw_token, token_hash, expires_at). Caller persists
    token_hash (never raw_token) in the refresh_tokens table."""
    now = datetime.now(timezone.utc)
    expires_at = now + _token_expiry("refresh", settings)
    jti = secrets.token_urlsafe(32)

    payload: dict[str, Any] = {
        "sub": subject,
        "type": "refresh",
        "jti": jti,
        "iat": now,
        "exp": expires_at,
    }
    raw_token = jwt.encode(payload, settings.api_secret_key, algorithm=settings.jwt_algorithm)
    return raw_token, hash_token(raw_token), expires_at


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    """Raises jwt.PyJWTError (or a subclass) on an invalid/expired token —
    callers translate that into UnauthorizedError."""
    return jwt.decode(token, settings.api_secret_key, algorithms=[settings.jwt_algorithm])
