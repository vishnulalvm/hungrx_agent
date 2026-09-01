"""Local filesystem storage backend — the default (`STORAGE_BACKEND=local`)
for development and single-server deployments. `key` is treated as a
relative path under `base_dir`; parent directories are created as needed.
"""

import asyncio
from pathlib import Path

from infrastructure.storage.base import StorageAdapter


class LocalStorageAdapter(StorageAdapter):
    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    def _resolve(self, key: str) -> Path:
        path = (self._base_dir / key).resolve()
        if self._base_dir.resolve() not in path.parents and path != self._base_dir.resolve():
            raise ValueError(f"Refusing to write outside storage base_dir: {key!r}")
        return path

    async def save(self, *, key: str, content: bytes) -> str:
        path = self._resolve(key)
        await asyncio.to_thread(self._write, path, content)
        return str(path)

    def _write(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    async def read(self, storage_path: str) -> bytes:
        return await asyncio.to_thread(Path(storage_path).read_bytes)
