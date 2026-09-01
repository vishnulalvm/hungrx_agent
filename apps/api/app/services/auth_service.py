"""Login / refresh / logout orchestration.

Kept as a plain service class (not route handlers directly) so the same
logic is reusable and independently testable without spinning up the ASGI
app — the auth router just becomes a thin adapter over this.
"""

import uuid
from datetime import datetime, timezone

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
    verify_password,
)
from core.config.exceptions import UnauthorizedError
from core.config.settings import Settings
from database.models.user import User
from database.repositories.refresh_token_repository import RefreshTokenRepository
from database.repositories.user_repository import UserRepository


class TokenPair:
    def __init__(self, access_token: str, refresh_token: str, user: User) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        # Carried alongside the tokens so callers (e.g. the auth router's
        # audit logging) never need a second DB round trip just to know
        # whose session this is.
        self.user = user


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)

    async def authenticate(self, *, email: str, password: str) -> User:
        user = await self._users.get_by_email(email)
        # Verify against a hash even on a missing user, so response timing
        # doesn't leak whether an email is registered.
        dummy_hash = "$2b$12$CwTycUXWue0Thq9StjUM0uJ8i7C.QoJ.9E.4mIYW8O7bH8YQb1x.G"
        if user is None:
            verify_password(password, dummy_hash)
            raise UnauthorizedError("Incorrect email or password")

        if not verify_password(password, user.hashed_password):
            raise UnauthorizedError("Incorrect email or password")

        if not user.is_active:
            raise UnauthorizedError("This account has been deactivated")

        return user

    async def issue_tokens(self, user: User) -> TokenPair:
        access_token = create_access_token(
            subject=str(user.id), role=user.role.value, settings=self._settings
        )
        raw_refresh, refresh_hash, expires_at = create_refresh_token(
            subject=str(user.id), settings=self._settings
        )
        await self._refresh_tokens.create(
            user_id=user.id, token_hash=refresh_hash, expires_at=expires_at
        )
        return TokenPair(access_token=access_token, refresh_token=raw_refresh, user=user)

    async def refresh(self, *, raw_refresh_token: str) -> TokenPair:
        try:
            payload = decode_token(raw_refresh_token, self._settings)
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Invalid refresh token") from exc

        if payload.get("type") != "refresh":
            raise UnauthorizedError("Refresh token required")

        token_hash = hash_token(raw_refresh_token)
        record = await self._refresh_tokens.get_by_hash(token_hash)

        if record is None or record.revoked:
            raise UnauthorizedError("Refresh token has been revoked")
        if record.expires_at < datetime.now(timezone.utc):
            raise UnauthorizedError("Refresh token has expired")

        user = await self._users.get_by_id(record.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Account is no longer active")

        # Rotate: the presented refresh token is single-use.
        await self._refresh_tokens.revoke(token_hash)
        return await self.issue_tokens(user)

    async def logout(self, raw_refresh_token: str) -> User | None:
        """Revokes the token and returns the owning user (for audit
        logging), when the token hash resolves to a known, non-revoked
        record — a garbage or already-revoked token still returns 204
        (logout is idempotent) but yields no user to attribute the event
        to."""
        token_hash = hash_token(raw_refresh_token)
        record = await self._refresh_tokens.get_by_hash(token_hash)
        await self._refresh_tokens.revoke(token_hash)
        if record is None:
            return None
        return await self._users.get_by_id(record.user_id)

    async def logout_all_sessions(self, user_id: uuid.UUID) -> None:
        await self._refresh_tokens.revoke_all_for_user(user_id)
