"""Unit tests for IngestionQueueRepository — the manual/verified
ingestion queue's persistence layer. Covers the in-batch duplicate-name
dedup, the QUEUED-only edit/delete guarantee, and claim_next_queued's
FOR UPDATE SKIP LOCKED claim-and-transition-to-RUNNING behavior, since
these are exactly the invariants the admin API/dispatcher depend on.
"""

import pytest

from core.schemas.ingestion_queue import IngestionQueueItemInput, IngestionQueueStatus
from database.repositories.ingestion_queue_repository import (
    InvalidQueueStateError,
    IngestionQueueRepository,
)

pytestmark = pytest.mark.asyncio


def _item(name: str, url: str = "https://example-restaurant.com") -> IngestionQueueItemInput:
    return IngestionQueueItemInput(name=name, official_url=url)


class TestCreateBatch:
    async def test_dedups_by_case_insensitive_trimmed_name(self, db_session) -> None:
        repo = IngestionQueueRepository(db_session)
        result = await repo.create_batch(
            items=[_item("Joe's Diner"), _item("  joe's diner  "), _item("Other Place")],
            created_by_user_id=None,
        )

        assert len(result.created) == 2
        assert result.skipped_duplicate_names == ["  joe's diner  "]
        assert {r.name for r in result.created} == {"Joe's Diner", "Other Place"}

    async def test_all_created_rows_share_batch_id_and_start_queued(self, db_session) -> None:
        repo = IngestionQueueRepository(db_session)
        result = await repo.create_batch(items=[_item("A"), _item("B")], created_by_user_id=None)

        assert all(r.batch_id == result.batch_id for r in result.created)
        assert all(r.status == IngestionQueueStatus.QUEUED for r in result.created)


class TestUpdateAndDeleteIfQueued:
    async def test_update_succeeds_while_queued(self, db_session) -> None:
        repo = IngestionQueueRepository(db_session)
        batch = await repo.create_batch(items=[_item("A")], created_by_user_id=None)
        item_id = batch.created[0].id

        updated = await repo.update_if_queued(item_id, name="A Renamed")
        assert updated.name == "A Renamed"

    async def test_update_rejected_once_not_queued(self, db_session) -> None:
        repo = IngestionQueueRepository(db_session)
        batch = await repo.create_batch(items=[_item("A")], created_by_user_id=None)
        item_id = batch.created[0].id

        claimed = await repo.claim_next_queued()
        assert claimed is not None and claimed.id == item_id

        with pytest.raises(InvalidQueueStateError):
            await repo.update_if_queued(item_id, name="Should not apply")

    async def test_delete_succeeds_while_queued(self, db_session) -> None:
        repo = IngestionQueueRepository(db_session)
        batch = await repo.create_batch(items=[_item("A")], created_by_user_id=None)
        item_id = batch.created[0].id

        await repo.delete_if_queued(item_id)
        assert await repo.get_by_id(item_id) is None

    async def test_delete_rejected_once_running(self, db_session) -> None:
        repo = IngestionQueueRepository(db_session)
        batch = await repo.create_batch(items=[_item("A")], created_by_user_id=None)
        item_id = batch.created[0].id

        await repo.claim_next_queued()

        with pytest.raises(InvalidQueueStateError):
            await repo.delete_if_queued(item_id)

    async def test_update_rejected_for_nonexistent_item(self, db_session) -> None:
        import uuid

        repo = IngestionQueueRepository(db_session)
        with pytest.raises(InvalidQueueStateError):
            await repo.update_if_queued(uuid.uuid4(), name="X")


class TestRequeueIfFailed:
    async def test_requeue_resets_status_and_clears_error(self, db_session) -> None:
        repo = IngestionQueueRepository(db_session)
        batch = await repo.create_batch(items=[_item("A")], created_by_user_id=None)
        item_id = batch.created[0].id

        await repo.claim_next_queued()
        await repo.mark_failed(item_id, error_message="boom")

        requeued = await repo.requeue_if_failed(item_id)
        assert requeued.status == IngestionQueueStatus.QUEUED
        assert requeued.error_message is None

    async def test_requeue_rejected_unless_failed(self, db_session) -> None:
        repo = IngestionQueueRepository(db_session)
        batch = await repo.create_batch(items=[_item("A")], created_by_user_id=None)
        item_id = batch.created[0].id

        with pytest.raises(InvalidQueueStateError):
            await repo.requeue_if_failed(item_id)


class TestClaimNextQueued:
    async def test_returns_none_when_empty(self, db_session) -> None:
        assert await IngestionQueueRepository(db_session).claim_next_queued() is None

    async def test_claims_oldest_queued_and_marks_running(self, db_session) -> None:
        repo = IngestionQueueRepository(db_session)
        batch = await repo.create_batch(items=[_item("First"), _item("Second")], created_by_user_id=None)

        claimed = await repo.claim_next_queued()

        assert claimed is not None
        assert claimed.id in {r.id for r in batch.created}
        assert claimed.status == IngestionQueueStatus.RUNNING

    async def test_does_not_reclaim_an_already_running_row(self, db_session) -> None:
        repo = IngestionQueueRepository(db_session)
        await repo.create_batch(items=[_item("Only")], created_by_user_id=None)

        first_claim = await repo.claim_next_queued()
        second_claim = await repo.claim_next_queued()

        assert first_claim is not None
        assert second_claim is None
