"""Turns a raw FetchResult into a persisted SourceSnapshot: hash the
content, store the bytes, build the typed metadata record. No AI
extraction happens here — this is purely "capture and record what we
fetched," the boundary the task explicitly draws.
"""

import uuid
from datetime import datetime, timezone

from core.schemas.source import SourceSnapshot
from infrastructure.crawler.fetch_result import FetchResult
from infrastructure.crawler.hashing import sha256_hex
from infrastructure.storage.base import StorageAdapter


class SnapshotService:
    def __init__(self, storage: StorageAdapter) -> None:
        self._storage = storage

    async def store_snapshot(self, *, source_id: uuid.UUID, result: FetchResult) -> SourceSnapshot:
        content_hash = sha256_hex(result.content)
        extension = {
            "html": "html",
            "pdf": "pdf",
            "screenshot": "png",
        }[result.content_type.value]
        key = f"{source_id}/{content_hash}.{extension}"

        storage_path = await self._storage.save(key=key, content=result.content)

        return SourceSnapshot(
            source_id=source_id,
            content_type=result.content_type,
            content_hash=content_hash,
            storage_path=storage_path,
            fetched_at=datetime.now(timezone.utc),
            http_status=result.http_status,
            content_length_bytes=result.content_length_bytes,
        )
