"""Unit tests for the reviewer workflow's Temporal Hash Polling node —
run against a real Postgres transaction (see tests/conftest.py) with a
fake RootPageFetcher, so behavior is exercised through the actual node
function without any real network call.

Covers: first-ever poll (no prior snapshot) always treated as changed,
matching hashes report unchanged, differing hashes report changed, the
freshly polled snapshot is always persisted regardless of outcome, and
AgentRun/AuditLog bookkeeping for both the unchanged and failure paths.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from core.schemas.agent_run import AgentRunStatus
from core.schemas.restaurant import Restaurant, RestaurantLocation
from core.schemas.source import SnapshotContentType, Source, SourceSnapshot, SourceType
from database.models.agent_run import AgentRun
from database.models.source_snapshot import SourceSnapshotRow
from database.repositories.source_repository import SourceRepository
from database.repositories.source_snapshot_repository import SourceSnapshotRepository
from workflows.reviewer_workflow.nodes.temporal_hash_polling import build_temporal_hash_polling_node

pytestmark = pytest.mark.asyncio


class FakeRootPageFetcher:
    def __init__(self, *, content_hash: str, source_id: uuid.UUID) -> None:
        self._content_hash = content_hash
        self._source_id = source_id
        self.calls: list[str] = []

    async def fetch_root(self, *, source_id: uuid.UUID, url: str) -> SourceSnapshot:
        self.calls.append(url)
        return SourceSnapshot(
            source_id=self._source_id,
            content_type=SnapshotContentType.HTML,
            content_hash=self._content_hash,
            storage_path=f"/fake/{url}",
            fetched_at=datetime.now(timezone.utc),
            http_status=200,
            content_length_bytes=1234,
        )


class FailingRootPageFetcher:
    async def fetch_root(self, *, source_id: uuid.UUID, url: str) -> SourceSnapshot:
        raise RuntimeError("simulated network failure")


def _restaurant() -> Restaurant:
    return Restaurant(
        name="Joe's Pizza",
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
    )


async def _persisted_source(db_session, restaurant_id: uuid.UUID) -> Source:
    """Persists a real sources row and returns it as the Pydantic Source
    shape the node expects on state — source_snapshots.source_id is a
    real FK, so any snapshot-persisting test needs a real Source row to
    reference, not just an in-memory Pydantic instance."""
    row = await SourceRepository(db_session).create(
        restaurant_id=restaurant_id,
        source_type=SourceType.RESTAURANT_WEBSITE,
        url="https://joespizza.example.com",
        is_verified_domain=True,
    )
    return Source(
        id=row.id,
        restaurant_id=row.restaurant_id,
        source_type=row.source_type,
        url=row.url,
        is_verified_domain=row.is_verified_domain,
    )


class TestFirstEverPoll:
    async def test_no_prior_snapshot_is_treated_as_changed(self, db_session) -> None:
        restaurant = _restaurant()
        source = await _persisted_source(db_session, restaurant.id)
        fetcher = FakeRootPageFetcher(content_hash="a" * 64, source_id=source.id)
        node = build_temporal_hash_polling_node(
            db_session, storage=None, settings=None, fetcher_factory=lambda domain: fetcher
        )

        update = await node({"restaurant": restaurant, "source": source})

        assert update["hash_changed"] is True
        assert update["previous_content_hash"] is None
        assert update["current_content_hash"] == "a" * 64

    async def test_persists_the_freshly_polled_snapshot(self, db_session) -> None:
        restaurant = _restaurant()
        source = await _persisted_source(db_session, restaurant.id)
        fetcher = FakeRootPageFetcher(content_hash="a" * 64, source_id=source.id)
        node = build_temporal_hash_polling_node(
            db_session, storage=None, settings=None, fetcher_factory=lambda domain: fetcher
        )

        await node({"restaurant": restaurant, "source": source})

        rows = await db_session.execute(
            select(SourceSnapshotRow).where(SourceSnapshotRow.source_id == source.id)
        )
        assert len(rows.scalars().all()) == 1


class TestSubsequentPoll:
    async def test_matching_hash_reports_unchanged(self, db_session) -> None:
        restaurant = _restaurant()
        source = await _persisted_source(db_session, restaurant.id)
        await SourceSnapshotRepository(db_session).create(
            SourceSnapshot(
                source_id=source.id,
                content_type=SnapshotContentType.HTML,
                content_hash="a" * 64,
                storage_path="/fake/prior",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        fetcher = FakeRootPageFetcher(content_hash="a" * 64, source_id=source.id)
        node = build_temporal_hash_polling_node(
            db_session, storage=None, settings=None, fetcher_factory=lambda domain: fetcher
        )

        update = await node({"restaurant": restaurant, "source": source})

        assert update["hash_changed"] is False
        assert update["previous_content_hash"] == "a" * 64

    async def test_differing_hash_reports_changed(self, db_session) -> None:
        restaurant = _restaurant()
        source = await _persisted_source(db_session, restaurant.id)
        await SourceSnapshotRepository(db_session).create(
            SourceSnapshot(
                source_id=source.id,
                content_type=SnapshotContentType.HTML,
                content_hash="a" * 64,
                storage_path="/fake/prior",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        fetcher = FakeRootPageFetcher(content_hash="b" * 64, source_id=source.id)
        node = build_temporal_hash_polling_node(
            db_session, storage=None, settings=None, fetcher_factory=lambda domain: fetcher
        )

        update = await node({"restaurant": restaurant, "source": source})

        assert update["hash_changed"] is True

    async def test_unchanged_marks_agent_run_succeeded(self, db_session) -> None:
        restaurant = _restaurant()
        source = await _persisted_source(db_session, restaurant.id)
        await SourceSnapshotRepository(db_session).create(
            SourceSnapshot(
                source_id=source.id,
                content_type=SnapshotContentType.HTML,
                content_hash="a" * 64,
                storage_path="/fake/prior",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        fetcher = FakeRootPageFetcher(content_hash="a" * 64, source_id=source.id)
        node = build_temporal_hash_polling_node(
            db_session, storage=None, settings=None, fetcher_factory=lambda domain: fetcher
        )

        update = await node({"restaurant": restaurant, "source": source})

        run_row = await db_session.get(AgentRun, uuid.UUID(update["agent_run_id"]))
        assert run_row.status == AgentRunStatus.SUCCEEDED


class TestFetchFailure:
    async def test_fetch_failure_reports_an_error_and_marks_agent_run_failed(self, db_session) -> None:
        restaurant = _restaurant()
        source = await _persisted_source(db_session, restaurant.id)
        node = build_temporal_hash_polling_node(
            db_session, storage=None, settings=None, fetcher_factory=lambda domain: FailingRootPageFetcher()
        )

        update = await node({"restaurant": restaurant, "source": source})

        assert "hash_changed" not in update
        assert len(update["errors"]) == 1
        run_row = await db_session.get(AgentRun, uuid.UUID(update["agent_run_id"]))
        assert run_row.status == AgentRunStatus.FAILED


class TestFailsClosedWithoutInput:
    async def test_missing_source_reports_an_error(self, db_session) -> None:
        node = build_temporal_hash_polling_node(db_session, storage=None, settings=None)

        update = await node({"restaurant": _restaurant()})

        assert "agent_run_id" not in update
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "temporal_hash_polling"
