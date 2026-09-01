import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.source import SnapshotContentType, SourceSnapshot
from database.models.source_snapshot import SourceSnapshotRow


class SourceSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, snapshot: SourceSnapshot) -> SourceSnapshotRow:
        """Persists an already-captured core.schemas.source.SourceSnapshot
        (same id, so callers that already hold the Pydantic object can
        cross-reference it against the row this returns)."""
        record = SourceSnapshotRow(
            id=snapshot.id,
            source_id=snapshot.source_id,
            content_type=snapshot.content_type,
            content_hash=snapshot.content_hash,
            storage_path=snapshot.storage_path,
            fetched_at=snapshot.fetched_at,
            http_status=snapshot.http_status,
            content_length_bytes=snapshot.content_length_bytes,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_latest_for_source(
        self, source_id: uuid.UUID, *, content_type: SnapshotContentType | None = None
    ) -> SourceSnapshotRow | None:
        """The most recently fetched snapshot for a source — what
        Temporal Hash Polling compares its fresh fetch's hash against.
        `content_type` narrows to e.g. only HTML root-page snapshots when
        a caller cares specifically about that (screenshots/PDFs hash
        independently and shouldn't be conflated with the root page)."""
        query = select(SourceSnapshotRow).where(SourceSnapshotRow.source_id == source_id)
        if content_type is not None:
            query = query.where(SourceSnapshotRow.content_type == content_type)
        query = query.order_by(desc(SourceSnapshotRow.fetched_at)).limit(1)
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def list_for_source(self, source_id: uuid.UUID) -> list[SourceSnapshotRow]:
        result = await self._session.execute(
            select(SourceSnapshotRow)
            .where(SourceSnapshotRow.source_id == source_id)
            .order_by(desc(SourceSnapshotRow.fetched_at))
        )
        return list(result.scalars().all())
