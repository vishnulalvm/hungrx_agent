"""Unit tests for the ingestion queue dispatcher's orchestration logic:
claim-one-item, run its pipeline, mark succeeded/failed, and the
strictly-one-at-a-time guarantee (a second queued item is left untouched
until the first resolves). The underlying pipeline stages
(verify/crawl/collector-workflow) are exercised by their own existing
test suites; here they're patched out so this test targets only the
dispatcher's own new orchestration behavior — claiming, status
transitions, and the sequential self-enqueue decision.
"""

from unittest.mock import AsyncMock, patch

import pytest

from core.schemas.ingestion_queue import IngestionQueueItemInput, IngestionQueueStatus
from database.repositories.ingestion_queue_repository import IngestionQueueRepository

pytestmark = pytest.mark.asyncio


class _NonClosingSessionCtx:
    """`_dispatch_one_cycle_async` opens several `async with
    session_factory() as session` blocks per cycle (claim, mark-succeeded/
    failed) — real
    session_factory() calls create a fresh AsyncSession each time, but
    the test's shared `db_session` fixture must survive all of them
    (AsyncSession.__aexit__ calls close(), which would tear down the
    fixture's connection after the first block). This wraps the shared
    session so `__aexit__` is a no-op instead."""

    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info) -> None:
        return None


def _item(name: str) -> IngestionQueueItemInput:
    return IngestionQueueItemInput(name=name, official_url="https://example-restaurant.com")


class TestDispatchOneCycle:
    async def test_empty_queue_returns_empty_status_without_self_enqueue(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(
            "apps.worker.app.jobs.ingestion_queue_dispatcher.get_sessionmaker",
            lambda: (lambda: _NonClosingSessionCtx(db_session)),
        )

        from apps.worker.app.jobs import ingestion_queue_dispatcher as dispatcher

        with patch.object(dispatcher, "get_queue") as mock_get_queue:
            result = await dispatcher._dispatch_one_cycle_async()

        assert result == {"status": "empty"}
        mock_get_queue.assert_not_called()

    async def test_successful_item_is_marked_succeeded_and_reenqueues(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(
            "apps.worker.app.jobs.ingestion_queue_dispatcher.get_sessionmaker",
            lambda: (lambda: _NonClosingSessionCtx(db_session)),
        )

        repo = IngestionQueueRepository(db_session)
        batch = await repo.create_batch(items=[_item("A")], created_by_user_id=None)
        await db_session.commit()
        item_id = batch.created[0].id

        from apps.worker.app.jobs import ingestion_queue_dispatcher as dispatcher

        fake_outcome = {
            "restaurant_id": "11111111-1111-1111-1111-111111111111",
            "source_id": "22222222-2222-2222-2222-222222222222",
            "source_snapshot_id": "33333333-3333-3333-3333-333333333333",
            "agent_run_id": None,
            "published_restaurant_id": None,
            "errors": [],
        }

        with patch.object(dispatcher, "_run_one_item", AsyncMock(return_value=fake_outcome)), patch.object(
            dispatcher, "get_queue"
        ) as mock_get_queue:
            result = await dispatcher._dispatch_one_cycle_async()

        assert result["status"] == "succeeded"
        mock_get_queue.return_value.enqueue.assert_called_once_with(
            dispatcher.run_dispatch_next_ingestion_queue_item
        )

        refreshed = await repo.get_by_id(item_id)
        assert refreshed.status == IngestionQueueStatus.SUCCEEDED

    async def test_failed_item_is_marked_failed_with_error_and_still_reenqueues(
        self, db_session, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "apps.worker.app.jobs.ingestion_queue_dispatcher.get_sessionmaker",
            lambda: (lambda: _NonClosingSessionCtx(db_session)),
        )

        repo = IngestionQueueRepository(db_session)
        batch = await repo.create_batch(items=[_item("A")], created_by_user_id=None)
        await db_session.commit()
        item_id = batch.created[0].id

        from apps.worker.app.jobs import ingestion_queue_dispatcher as dispatcher

        with patch.object(
            dispatcher, "_run_one_item", AsyncMock(side_effect=RuntimeError("crawl failed"))
        ), patch.object(dispatcher, "get_queue") as mock_get_queue:
            result = await dispatcher._dispatch_one_cycle_async()

        assert result["status"] == "failed"
        mock_get_queue.return_value.enqueue.assert_called_once()

        refreshed = await repo.get_by_id(item_id)
        assert refreshed.status == IngestionQueueStatus.FAILED
        assert refreshed.error_message == "crawl failed"

    async def test_only_one_item_claimed_per_cycle_leaving_the_second_untouched(
        self, db_session, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "apps.worker.app.jobs.ingestion_queue_dispatcher.get_sessionmaker",
            lambda: (lambda: _NonClosingSessionCtx(db_session)),
        )

        repo = IngestionQueueRepository(db_session)
        batch = await repo.create_batch(items=[_item("First"), _item("Second")], created_by_user_id=None)
        await db_session.commit()

        from apps.worker.app.jobs import ingestion_queue_dispatcher as dispatcher

        fake_outcome = {
            "restaurant_id": "11111111-1111-1111-1111-111111111111",
            "source_id": "22222222-2222-2222-2222-222222222222",
            "source_snapshot_id": "33333333-3333-3333-3333-333333333333",
            "agent_run_id": None,
            "published_restaurant_id": None,
            "errors": [],
        }

        with patch.object(dispatcher, "_run_one_item", AsyncMock(return_value=fake_outcome)), patch.object(
            dispatcher, "get_queue"
        ):
            await dispatcher._dispatch_one_cycle_async()

        statuses = {item.name: item.status for item in [await repo.get_by_id(i.id) for i in batch.created]}
        succeeded_count = sum(1 for s in statuses.values() if s == IngestionQueueStatus.SUCCEEDED)
        queued_count = sum(1 for s in statuses.values() if s == IngestionQueueStatus.QUEUED)

        assert succeeded_count == 1
        assert queued_count == 1
