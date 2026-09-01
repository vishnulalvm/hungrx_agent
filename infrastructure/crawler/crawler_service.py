"""Top-level crawler entry point: ties domain verification/locking,
HTTP-first fetching (falling back to browser automation only when
explicitly requested), metadata extraction, and snapshot storage into one
call. This is the public surface other modules (e.g. the future LangGraph
collector_workflow) should use rather than reaching into the individual
fetcher/storage classes directly.
"""

import uuid

from core.config.settings import Settings
from core.schemas.source import SnapshotContentType, SourceSnapshot
from infrastructure.crawler.browser_fetcher import BrowserFetcher
from infrastructure.crawler.domain_lock import DomainLock, DomainVerifier
from infrastructure.crawler.http_fetcher import HttpFetcher
from infrastructure.crawler.metadata import PageMetadata, extract_page_metadata
from infrastructure.crawler.snapshot_service import SnapshotService
from infrastructure.storage.base import StorageAdapter


class CrawlerService:
    """One instance per crawl job, scoped to a single verified restaurant
    domain — `domain_lock` may be shared across jobs (it's keyed
    internally by domain), but `domain_verifier` must not be, since it's
    what enforces the domain boundary for this specific job."""

    def __init__(
        self,
        *,
        verified_domain: str,
        storage: StorageAdapter,
        settings: Settings,
        domain_lock: DomainLock | None = None,
    ) -> None:
        self._domain_verifier = DomainVerifier(verified_domain)
        self._domain_lock = domain_lock or DomainLock()
        self._settings = settings
        self._snapshots = SnapshotService(storage)

    async def fetch_and_store(
        self, *, source_id: uuid.UUID, url: str, use_browser: bool = False
    ) -> tuple[SourceSnapshot, PageMetadata | None]:
        """Fetches `url` (HTML or PDF via httpx by default; only spins up
        Playwright when `use_browser=True`, since browser automation is
        materially more expensive and should be an explicit opt-in for
        pages that actually need JS rendering), stores the result, and
        returns the snapshot plus extracted page metadata (metadata is
        None for PDFs — there's no <head> to parse).
        """
        if use_browser:
            async with BrowserFetcher(
                domain_verifier=self._domain_verifier,
                domain_lock=self._domain_lock,
                user_agent=self._settings.crawler_user_agent,
                headless=self._settings.playwright_headless,
            ) as browser:
                result = await browser.fetch_rendered_html(url)
        else:
            async with HttpFetcher(
                domain_verifier=self._domain_verifier,
                domain_lock=self._domain_lock,
                user_agent=self._settings.crawler_user_agent,
            ) as fetcher:
                result = await fetcher.fetch(url)

        snapshot = await self._snapshots.store_snapshot(source_id=source_id, result=result)

        metadata = None
        if result.content_type == SnapshotContentType.HTML:
            metadata = extract_page_metadata(result.content.decode("utf-8", errors="replace"))

        return snapshot, metadata

    async def capture_screenshot(self, *, source_id: uuid.UUID, url: str) -> SourceSnapshot:
        async with BrowserFetcher(
            domain_verifier=self._domain_verifier,
            domain_lock=self._domain_lock,
            user_agent=self._settings.crawler_user_agent,
            headless=self._settings.playwright_headless,
        ) as browser:
            result = await browser.capture_screenshot(url)

        return await self._snapshots.store_snapshot(source_id=source_id, result=result)
