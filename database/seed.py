"""Bootstraps the first SUPER_ADMIN account so there's a way into the admin
dashboard on a fresh database. Idempotent — safe to run more than once.

Usage: uv run python -m database.seed
Reads SEED_SUPER_ADMIN_EMAIL / SEED_SUPER_ADMIN_PASSWORD from the
environment, falling back to a clearly-fake default for local dev only.
"""

import asyncio
import os

from apps.api.app.core.security import hash_password
from core.schemas.auth import Role
from database.models.user import User
from database.repositories.user_repository import UserRepository
from database.session import get_sessionmaker


async def seed_super_admin() -> None:
    email = os.environ.get("SEED_SUPER_ADMIN_EMAIL", "admin@hungrx.local")
    password = os.environ.get("SEED_SUPER_ADMIN_PASSWORD", "change-me-immediately")

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        repo = UserRepository(session)
        existing = await repo.get_by_email(email)
        if existing is not None:
            print(f"Super admin already exists: {email}")
            return

        user = User(
            email=email.lower(),
            hashed_password=hash_password(password),
            full_name="Super Admin",
            role=Role.SUPER_ADMIN,
            is_active=True,
        )
        await repo.create(user)
        await session.commit()
        print(f"Created super admin: {email}")


if __name__ == "__main__":
    asyncio.run(seed_super_admin())
