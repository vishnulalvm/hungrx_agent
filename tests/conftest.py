"""Shared pytest fixtures for authentication/authorization tests.

Tests run against a real Postgres database (hungrx_test on the same
Postgres server as dev — never the dev database itself) so ORM behavior,
constraints, and the enum column are exercised exactly as in production;
SQLite would silently accept things Postgres wouldn't. Each test runs
inside a transaction that's rolled back afterward, so tests never leak
state into each other regardless of execution order.
"""

import os

# Set before any app import below (Settings() is lru_cache'd via
# get_settings() — this must land before the first call, anywhere,
# during test collection/fixture setup) so apps.api.app.core.rate_limit's
# login/refresh rate limiter — otherwise correctly throttling — doesn't
# also throttle the test suite's own rapid-fire login calls, which all
# share one "IP" through the ASGI test transport. Assigned unconditionally
# (not setdefault) since the dev container sets ENVIRONMENT=development
# as a real process env var — the test run always overrides it to "test"
# regardless of what the container itself was started with.
os.environ["ENVIRONMENT"] = "test"

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.api.app.core.security import hash_password
from core.schemas.auth import Role
from database.models.base import Base
from database.models.user import User
from infrastructure.checkpointer import _to_psycopg_dsn

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/hungrx_test",
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema() -> AsyncIterator[None]:
    """Creates every table once per test run and drops them at the end.
    A dedicated short-lived engine — NOT the per-test `engine` fixture
    below — so this session-scoped setup never shares an asyncpg
    connection across the function-scoped event loops pytest-asyncio
    spins up per test (mixing loop scopes on one asyncpg connection is
    what caused the "another operation is in progress" failures)."""
    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await eng.dispose()

    yield

    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Function-scoped so its connections always belong to the same event
    loop as the test using them — asyncpg connections are not safe to
    reuse across event loops, and pytest-asyncio's default fixture loop
    scope is per-function."""
    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One outer transaction per test, rolled back on teardown — so
    fixtures like `super_admin_user` below can commit-as-usual from the
    app's point of view while still leaving no trace between tests."""
    connection = await engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    session = session_factory()

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()


@pytest_asyncio.fixture
async def checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """A real Postgres-backed checkpointer against TEST_DATABASE_URL —
    needed for anything exercising the collector workflow's human-review
    interrupt/resume (see workflows/collector_workflow/nodes/
    human_review.py), since that's the whole reason a durable checkpointer
    exists at all; an in-memory one wouldn't exercise the same
    cross-connection persistence a real API request/resume pair relies on.

    This connection is intentionally separate from `db_session`'s
    savepoint-rolled-back transaction — the checkpointer writes through
    its own psycopg connection, so checkpoint rows are NOT rolled back
    automatically. `checkpoint_ns`-scoped `delete_thread` calls after each
    test that used a distinguishable thread_id keep this from
    accumulating, but the safest pattern (used throughout the collector
    workflow tests) is to always use a fresh, random thread_id per test
    rather than relying on cleanup ordering.
    """
    dsn = _to_psycopg_dsn(TEST_DATABASE_URL)
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
        yield saver


@pytest_asyncio.fixture
async def app_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An httpx AsyncClient wired directly to the FastAPI app via
    ASGITransport (no real network socket), with the DB session dependency
    overridden to the per-test transactional session above."""
    from apps.api.app.dependencies.db import get_db_session
    from apps.api.app.main import app

    async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()


async def _make_user(db_session: AsyncSession, *, role: Role, email: str, password: str) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=role.value.title(),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def user_password() -> str:
    return "correct-horse-battery-staple"


@pytest_asyncio.fixture
async def super_admin_user(db_session: AsyncSession, user_password: str) -> User:
    return await _make_user(
        db_session,
        role=Role.SUPER_ADMIN,
        email=f"super-{uuid.uuid4().hex[:8]}@hungrx.example",
        password=user_password,
    )


@pytest_asyncio.fixture
async def data_manager_user(db_session: AsyncSession, user_password: str) -> User:
    return await _make_user(
        db_session,
        role=Role.DATA_MANAGER,
        email=f"datamgr-{uuid.uuid4().hex[:8]}@hungrx.example",
        password=user_password,
    )


@pytest_asyncio.fixture
async def reviewer_user(db_session: AsyncSession, user_password: str) -> User:
    return await _make_user(
        db_session,
        role=Role.REVIEWER,
        email=f"reviewer-{uuid.uuid4().hex[:8]}@hungrx.example",
        password=user_password,
    )


@pytest_asyncio.fixture
async def viewer_user(db_session: AsyncSession, user_password: str) -> User:
    return await _make_user(
        db_session,
        role=Role.VIEWER,
        email=f"viewer-{uuid.uuid4().hex[:8]}@hungrx.example",
        password=user_password,
    )


@pytest_asyncio.fixture
async def inactive_user(db_session: AsyncSession, user_password: str) -> User:
    user = await _make_user(
        db_session,
        role=Role.VIEWER,
        email=f"inactive-{uuid.uuid4().hex[:8]}@hungrx.example",
        password=user_password,
    )
    user.is_active = False
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def login(client: AsyncClient, *, email: str, password: str) -> dict:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return response.json()


def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}
