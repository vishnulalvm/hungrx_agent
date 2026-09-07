import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.ingestion_queue import IngestionQueueItemInput, IngestionQueueStatus
from database.models.ingestion_queue_item import IngestionQueueItem


class InvalidQueueStateError(Exception):
    """Raised when an operation requires a row to be in a specific status
    (e.g. QUEUED for edit/delete, FAILED for requeue) and it isn't — either
    because no such row exists, or because it has already moved on."""


@dataclass(slots=True)
class BatchCreateResult:
    batch_id: uuid.UUID
    created: list[IngestionQueueItem] = field(default_factory=list)
    skipped_duplicate_names: list[str] = field(default_factory=list)


class IngestionQueueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_batch(
        self, *, items: list[IngestionQueueItemInput], created_by_user_id: uuid.UUID | None
    ) -> BatchCreateResult:
        """In-batch dedup by name (case-insensitive, whitespace-trimmed):
        the first occurrence of a name is queued, later occurrences are
        reported back as skipped and never persisted. No dedup against
        already-published restaurants or other batches — only within this
        one upload."""
        batch_id = uuid.uuid4()
        result = BatchCreateResult(batch_id=batch_id)
        seen_names: set[str] = set()

        for item in items:
            key = item.name.strip().lower()
            if key in seen_names:
                result.skipped_duplicate_names.append(item.name)
                continue
            seen_names.add(key)

            record = IngestionQueueItem(
                batch_id=batch_id,
                name=item.name,
                menu_url=item.menu_url,
                nutrition_url=item.nutrition_url,
                status=IngestionQueueStatus.QUEUED,
                created_by_user_id=created_by_user_id,
            )
            self._session.add(record)
            result.created.append(record)

        await self._session.flush()
        return result

    async def get_by_id(self, item_id: uuid.UUID) -> IngestionQueueItem | None:
        return await self._session.get(IngestionQueueItem, item_id)

    async def list_paginated(
        self, *, page: int, page_size: int, status: IngestionQueueStatus | None = None
    ) -> tuple[list[IngestionQueueItem], int]:
        filters = [IngestionQueueItem.status == status] if status is not None else []

        count_result = await self._session.execute(
            select(func.count()).select_from(IngestionQueueItem).where(*filters)
        )
        total = count_result.scalar_one()

        items_result = await self._session.execute(
            select(IngestionQueueItem)
            .where(*filters)
            .order_by(IngestionQueueItem.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(items_result.scalars().all()), total

    async def update_if_queued(self, item_id: uuid.UUID, **fields: object) -> IngestionQueueItem:
        """Only fields explicitly passed (non-None-filtered by the caller)
        are applied. Atomic: fetches with a row lock and re-checks status
        under that lock, rather than a plain get-then-write, so a
        concurrent claim_next_queued() can't flip this row to RUNNING
        between the check and the write."""
        record = await self._get_for_update(item_id)
        if record is None or record.status != IngestionQueueStatus.QUEUED:
            raise InvalidQueueStateError(f"Ingestion queue item {item_id} is not editable (not QUEUED)")

        for key, value in fields.items():
            if value is not None:
                setattr(record, key, value)

        await self._session.flush()
        # `updated_at` is server-computed (onupdate=func.now()) — the ORM
        # object's in-memory value is stale after flush until refreshed,
        # and a caller serializing this record (e.g. Pydantic's
        # model_validate) would otherwise trigger an implicit lazy load
        # outside any awaited context. refresh() re-fetches it explicitly
        # here, inside the same async call, instead.
        await self._session.refresh(record)
        return record

    async def delete_if_queued(self, item_id: uuid.UUID) -> None:
        record = await self._get_for_update(item_id)
        if record is None or record.status != IngestionQueueStatus.QUEUED:
            raise InvalidQueueStateError(f"Ingestion queue item {item_id} is not deletable (not QUEUED)")

        await self._session.delete(record)
        await self._session.flush()

    async def requeue_if_failed(self, item_id: uuid.UUID) -> IngestionQueueItem:
        record = await self._get_for_update(item_id)
        if record is None or record.status != IngestionQueueStatus.FAILED:
            raise InvalidQueueStateError(f"Ingestion queue item {item_id} is not requeueable (not FAILED)")

        record.status = IngestionQueueStatus.QUEUED
        record.error_message = None
        await self._session.flush()
        await self._session.refresh(record)
        return record

    async def claim_next_queued(self) -> IngestionQueueItem | None:
        """The dispatcher's core primitive: atomically claims the oldest
        QUEUED row and marks it RUNNING. `FOR UPDATE SKIP LOCKED` means a
        second concurrent claimant (if the worker fleet is ever scaled
        beyond one process) grabs a different row instead of blocking on
        or double-claiming this one."""
        result = await self._session.execute(
            select(IngestionQueueItem)
            .where(IngestionQueueItem.status == IngestionQueueStatus.QUEUED)
            .order_by(IngestionQueueItem.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None

        record.status = IngestionQueueStatus.RUNNING
        await self._session.flush()
        return record

    async def mark_succeeded(self, item_id: uuid.UUID) -> None:
        record = await self._session.get(IngestionQueueItem, item_id)
        if record is None:
            return
        record.status = IngestionQueueStatus.SUCCEEDED
        record.error_message = None
        await self._session.flush()

    async def mark_failed(self, item_id: uuid.UUID, *, error_message: str) -> None:
        record = await self._session.get(IngestionQueueItem, item_id)
        if record is None:
            return
        record.status = IngestionQueueStatus.FAILED
        record.error_message = error_message
        await self._session.flush()

    async def set_restaurant_seed_id(self, item_id: uuid.UUID, *, restaurant_seed_id: str) -> None:
        record = await self._session.get(IngestionQueueItem, item_id)
        if record is None:
            return
        record.restaurant_seed_id = restaurant_seed_id
        await self._session.flush()

    async def _get_for_update(self, item_id: uuid.UUID) -> IngestionQueueItem | None:
        result = await self._session.execute(
            select(IngestionQueueItem).where(IngestionQueueItem.id == item_id).with_for_update()
        )
        return result.scalar_one_or_none()
