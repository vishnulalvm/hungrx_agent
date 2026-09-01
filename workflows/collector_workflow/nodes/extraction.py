"""Extraction node (Collector Workflow Agent 2): captures raw source
material for the restaurant's verified website — HTML, PDFs, and
screenshots as needed — and persists it as SourceSnapshots. This node
does not interpret menu/nutrition values; it only identifies which pages
are relevant and records what was fetched. Turning captured content into
structured data is the job of later nodes (AI extraction/interpretation
is explicitly out of scope here, same as the graph skeleton's original
placeholder called out).

Responsibilities (per the collector workflow's Agent 2 spec):
  - inspect the verified source (state["source"] / state["source_url"],
    written by the source_authority node — this node never guesses a URL
    itself)
  - identify relevant menu/nutrition pages via deterministic link
    discovery (infrastructure.crawler.page_discovery), not AI
  - capture required source material: HTML via httpx, PDFs via httpx
    (content-type based), and a screenshot via Playwright only when the
    root page's HTML looks suspiciously thin (likely JS-rendered) —
    browser automation stays an explicit fallback, not the default path
  - persist snapshots via CrawlerService/SnapshotService (SHA-256 hashed,
    stored through the StorageAdapter)
  - return source references (SourceSnapshot records) to the graph —
    never the raw content itself, so state stays lightweight
  - keep raw extraction and AI interpretation separate: nothing here
    parses menu items, prices, or nutrition values out of the captured
    content

A LangGraph node function's signature is fixed to `(state) -> partial
state update`, so DB session / storage / settings can't be passed in
directly. `build_extraction_node` is a factory (same pattern as
source_authority) that closes over those dependencies. `page_fetcher` is
an additional injectable seam — production code defaults to a real
CrawlerService-backed fetcher, tests substitute a fake so this node's own
logic (page discovery, snapshot bookkeeping, error handling) can be
exercised without a network or a browser.
"""

import logging
import uuid
from typing import Any, Awaitable, Callable, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.config.settings import Settings
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.source import SnapshotContentType, Source, SourceSnapshot
from database.repositories.agent_run_repository import AgentRunRepository
from infrastructure.crawler.crawler_service import CrawlerService
from infrastructure.crawler.domain_lock import DomainLock, DomainVerifier
from infrastructure.crawler.page_discovery import find_menu_page_links
from infrastructure.storage.base import StorageAdapter
from workflows.collector_workflow.state import CollectorState

logger = logging.getLogger("hungrx.workflows.collector.extraction")

NODE_NAME = "extraction"

ExtractionNode = Callable[[CollectorState], Awaitable[dict[str, Any]]]

# A rendered page below this length is treated as likely client-side
# rendered (an empty app shell) and worth a browser-rendered fallback
# capture; a real menu-bearing page is essentially never this short.
_THIN_HTML_BYTES_THRESHOLD = 2_000


class PageCapture(Protocol):
    """One fetched-and-stored page: the persisted snapshot plus whatever
    HTML was fetched (empty for PDFs/screenshots) so the caller can run
    link discovery without a second round trip."""

    snapshot: SourceSnapshot
    html: str | None


class PageFetcher(Protocol):
    """Seam between the Extraction node's own logic (which pages to
    fetch, how many, how to record failures) and the actual network/
    browser work. Production uses `CrawlerServicePageFetcher`; tests use
    a fake implementing this same protocol."""

    async def fetch_html_or_pdf(self, *, source_id: uuid.UUID, url: str) -> PageCapture: ...

    async def fetch_screenshot(self, *, source_id: uuid.UUID, url: str) -> PageCapture: ...


class _Capture:
    def __init__(self, snapshot: SourceSnapshot, html: str | None) -> None:
        self.snapshot = snapshot
        self.html = html


class CrawlerServicePageFetcher:
    """Default PageFetcher backed by the real CrawlerService (httpx for
    HTML/PDF, Playwright only for the explicit screenshot fallback)."""

    def __init__(self, *, verified_domain: str, storage: StorageAdapter, settings: Settings) -> None:
        self._settings = settings
        self._storage = storage
        self._domain_lock = DomainLock()
        self._crawler = CrawlerService(
            verified_domain=verified_domain,
            storage=storage,
            settings=settings,
            domain_lock=self._domain_lock,
        )

    async def fetch_html_or_pdf(self, *, source_id: uuid.UUID, url: str) -> PageCapture:
        snapshot, _metadata = await self._crawler.fetch_and_store(source_id=source_id, url=url)
        html = None
        if snapshot.content_type == SnapshotContentType.HTML:
            # fetch_and_store persists the bytes but doesn't hand the raw
            # HTML back (only parsed metadata); page discovery needs the
            # actual markup, so read the just-stored bytes back from
            # storage rather than re-fetching over the network.
            content = await self._storage.read(snapshot.storage_path)
            html = content.decode("utf-8", errors="replace")
        return _Capture(snapshot=snapshot, html=html)

    async def fetch_screenshot(self, *, source_id: uuid.UUID, url: str) -> PageCapture:
        snapshot = await self._crawler.capture_screenshot(source_id=source_id, url=url)
        return _Capture(snapshot=snapshot, html=None)


def build_extraction_node(
    session: AsyncSession,
    storage: StorageAdapter,
    settings: Settings,
    *,
    page_fetcher_factory: Callable[[str], PageFetcher] | None = None,
) -> ExtractionNode:
    """`page_fetcher_factory(verified_domain) -> PageFetcher` defaults to
    a real CrawlerService-backed fetcher; pass a fake factory in tests to
    avoid any real network/browser calls while still exercising this
    node's own page-discovery and persistence-bookkeeping logic."""

    audit = AuditService(session)
    agent_runs = AgentRunRepository(session)
    factory = page_fetcher_factory or (
        lambda domain: CrawlerServicePageFetcher(verified_domain=domain, storage=storage, settings=settings)
    )

    async def extraction_node(state: CollectorState) -> dict[str, Any]:
        source: Source | None = state.get("source")
        source_url: str | None = state.get("source_url")

        if source is None or not source_url:
            message = "CollectorState.source/source_url is required before the extraction node runs (source_authority must succeed first)"
            logger.error("extraction node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        run_id = state.get("agent_run_id")
        fetcher = factory(source.url)

        try:
            snapshots = await _capture_source_material(fetcher, source=source, source_url=source_url)
        except Exception as exc:  # crawler/storage failures must not crash the graph run
            failure_message = f"extraction failed to capture source material for source_id={source.id}: {exc}"
            logger.warning(failure_message)
            if run_id is not None:
                await audit.log(
                    action=AuditAction.AGENT_RUN_TRIGGER,
                    entity_type=AuditEntityType.AGENT_RUN,
                    entity_id=run_id,
                    metadata={"node": NODE_NAME, "source_id": str(source.id), "error": str(exc)},
                )
                await agent_runs.mark_failed(uuid.UUID(run_id), error_message=failure_message)
            return {"errors": [{"node": NODE_NAME, "message": failure_message}]}

        if not snapshots:
            failure_message = f"extraction captured no snapshots for source_id={source.id}"
            logger.warning(failure_message)
            return {"errors": [{"node": NODE_NAME, "message": failure_message}]}

        return {
            "source_snapshot": snapshots[0],
            "source_snapshots": snapshots,
        }

    return extraction_node


async def _capture_source_material(
    fetcher: PageFetcher, *, source: Source, source_url: str
) -> list[SourceSnapshot]:
    """Fetches the source page itself, then (for HTML pages) discovers
    and fetches menu/nutrition-relevant linked pages. Every fetch is
    captured and persisted as a SourceSnapshot; no content is interpreted
    here — only whether a page looks relevant enough to capture."""

    root_capture = await fetcher.fetch_html_or_pdf(source_id=source.id, url=source_url)
    snapshots = [root_capture.snapshot]

    if root_capture.snapshot.content_type != SnapshotContentType.HTML or root_capture.html is None:
        # A PDF (or an HTML fetch whose body we couldn't read back) is
        # already the whole capture — there's no <head>/<a> structure to
        # run link discovery against.
        return snapshots

    if len(root_capture.html.encode("utf-8")) < _THIN_HTML_BYTES_THRESHOLD:
        # Likely a client-side-rendered shell; fall back to a
        # browser-rendered screenshot capture of the same page rather
        # than silently returning an near-empty snapshot.
        screenshot_capture = await fetcher.fetch_screenshot(source_id=source.id, url=source_url)
        snapshots.append(screenshot_capture.snapshot)

    domain_verifier = DomainVerifier(source.url)
    candidate_urls = find_menu_page_links(
        root_capture.html, base_url=source_url, domain_verifier=domain_verifier
    )

    for candidate_url in candidate_urls:
        capture = await fetcher.fetch_html_or_pdf(source_id=source.id, url=candidate_url)
        snapshots.append(capture.snapshot)

    return snapshots
