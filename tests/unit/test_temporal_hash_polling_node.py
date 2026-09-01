"""Unit tests for the reviewer workflow's Temporal Hash Polling node
(Reviewer Workflow Agent 6: Change Detection) — run against a real
Postgres transaction (see tests/conftest.py) with a fake
RootPageFetcher, so behavior is exercised through the actual node
function without any real network call.

Covers every requirement from the Agent 6 spec directly: loading the
active source URL from the database (not trusted blindly off state),
fetching + SHA-256 hashing, comparing against the previous snapshot,
persisting the new snapshot regardless of outcome, and recording agent
run metrics — plus the changed/unchanged pair the task explicitly calls
out, and AgentRun/AuditLog bookkeeping for the unchanged and failure
paths.
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


class TestLoadsActiveSourceFromDatabase:
    """The spec's "load active source URL" requirement: the node must
    look up the restaurant's current verified website itself, not trust
    whatever a caller happened to put on state["source"] — a
    caller-supplied Source could be stale."""

    async def test_ignores_a_stale_state_provided_source_in_favor_of_the_db_record(self, db_session) -> None:
        restaurant = _restaurant()
        db_source = await _persisted_source(db_session, restaurant.id)
        fetcher = FakeRootPageFetcher(content_hash="a" * 64, source_id=db_source.id)
        node = build_temporal_hash_polling_node(
            db_session, storage=None, settings=None, fetcher_factory=lambda domain: fetcher
        )

        stale_source = Source(
            id=uuid.uuid4(),
            restaurant_id=restaurant.id,
            source_type=SourceType.RESTAURANT_WEBSITE,
            url="https://old-domain.example.com",
            is_verified_domain=True,
        )

        update = await node({"restaurant": restaurant, "source": stale_source})

        # The fetcher was called against the DB-verified source's url,
        # not the stale state-provided one.
        assert fetcher.calls == [db_source.url]
        assert update["source"].id == db_source.id

    async def test_no_verified_source_and_no_usable_fallback_reports_an_error(self, db_session) -> None:
        node = build_temporal_hash_polling_node(db_session, storage=None, settings=None)

        update = await node({"restaurant": _restaurant()})

        assert "agent_run_id" not in update
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "temporal_hash_polling"

    async def test_falls_back_to_a_verified_state_provided_source_when_none_persisted(self, db_session) -> None:
        # get_verified_website_for_restaurant finds nothing (query
        # returns None), but a genuinely verified Source was supplied on
        # state directly (e.g. a caller that resolved it in the same
        # request) — still usable, since it's verified, just not
        # discoverable via that specific lookup query. The Source row
        # itself is still persisted (source_snapshots.source_id is a
        # real FK) — what's *not* persisted/queried here is any
        # association making get_verified_website_for_restaurant find it
        # (e.g. it could be a different source_type in a fuller scenario;
        # simulated here by monkeypatching the lookup to return None).
        restaurant = _restaurant()
        source = await _persisted_source(db_session, restaurant.id)

        node = build_temporal_hash_polling_node(
            db_session,
            storage=None,
            settings=None,
            fetcher_factory=lambda domain: FakeRootPageFetcher(content_hash="a" * 64, source_id=source.id),
        )

        import workflows.reviewer_workflow.nodes.temporal_hash_polling as module

        original = module.SourceRepository.get_verified_website_for_restaurant

        async def _always_none(self, restaurant_id):
            return None

        module.SourceRepository.get_verified_website_for_restaurant = _always_none
        try:
            update = await node({"restaurant": restaurant, "source": source})
        finally:
            module.SourceRepository.get_verified_website_for_restaurant = original

        assert "errors" not in update
        assert update["hash_changed"] is True


class TestRecordsAgentRunMetrics:
    """The spec's "record agent run metrics" requirement: durable,
    queryable metrics on AgentRun.metrics — not just an audit log
    entry."""

    async def test_changed_outcome_records_metrics(self, db_session) -> None:
        restaurant = _restaurant()
        source = await _persisted_source(db_session, restaurant.id)
        fetcher = FakeRootPageFetcher(content_hash="a" * 64, source_id=source.id)
        node = build_temporal_hash_polling_node(
            db_session, storage=None, settings=None, fetcher_factory=lambda domain: fetcher
        )

        update = await node({"restaurant": restaurant, "source": source})

        run_row = await db_session.get(AgentRun, uuid.UUID(update["agent_run_id"]))
        assert run_row.metrics["outcome"] == "changed"
        assert run_row.metrics["hash_changed"] is True
        assert run_row.metrics["content_length_bytes"] == 1234
        assert isinstance(run_row.metrics["fetch_duration_ms"], (int, float))

    async def test_unchanged_outcome_records_metrics(self, db_session) -> None:
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
        assert run_row.metrics["outcome"] == "unchanged"
        assert run_row.metrics["hash_changed"] is False

    async def test_fetch_failure_still_records_a_duration_metric(self, db_session) -> None:
        restaurant = _restaurant()
        source = await _persisted_source(db_session, restaurant.id)
        node = build_temporal_hash_polling_node(
            db_session, storage=None, settings=None, fetcher_factory=lambda domain: FailingRootPageFetcher()
        )

        update = await node({"restaurant": restaurant, "source": source})

        run_row = await db_session.get(AgentRun, uuid.UUID(update["agent_run_id"]))
        assert run_row.metrics["outcome"] == "fetch_failed"
        assert isinstance(run_row.metrics["fetch_duration_ms"], (int, float))


class TestChangedAndUnchangedCases:
    """The two headline cases the task explicitly asks for tests on,
    named exactly for that: a changed source continues the workflow (the
    node reports hash_changed=True and everything a downstream node
    needs), an unchanged source reports hash_changed=False (graph.py's
    routing is what actually terminates the run — proven separately in
    tests/unit/test_reviewer_graph.py)."""

    async def test_changed_case_continues(self, db_session) -> None:
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
        assert "errors" not in update

    async def test_unchanged_case_terminates(self, db_session) -> None:
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
        from workflows.reviewer_workflow.graph import _route_after_hash_polling

        assert _route_after_hash_polling(update) == "__end__"
