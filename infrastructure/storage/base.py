"""Storage adapter interface for persisting raw crawl captures (HTML
bytes, PDF bytes, screenshot PNGs). Concrete backends (local filesystem
now; S3/GCS could implement this same interface later) only need to
implement `save` and `read` — the snapshot service depends on this
interface, not any specific backend.
"""

from abc import ABC, abstractmethod


class StorageAdapter(ABC):
    @abstractmethod
    async def save(self, *, key: str, content: bytes) -> str:
        """Persists `content` under `key` and returns the storage path/URI
        that should be recorded as SourceSnapshot.storage_path."""

    @abstractmethod
    async def read(self, storage_path: str) -> bytes:
        """Reads back content previously saved at `storage_path`."""
