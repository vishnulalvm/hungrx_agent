"""Shared result type for every fetch path (httpx HTML/PDF, Playwright
browser, screenshot) — one shape the snapshot service can hash and store
regardless of which fetcher produced it."""

from dataclasses import dataclass

from core.schemas.source import SnapshotContentType


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    content_type: SnapshotContentType
    content: bytes
    http_status: int | None
    content_length_bytes: int
