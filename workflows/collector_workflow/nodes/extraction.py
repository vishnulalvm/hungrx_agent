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
    discovery (infrastructure.crawler.page_discovery) — never AI — run
    unconditionally against the menu root, plus a caller-supplied
    nutrition URL (state["nutrition_url"], manual ingestion only) fetched
    explicitly *in addition to* whatever discovery finds. A caller
    knowing the nutrition page's URL says nothing about whether the menu
    root itself already contains real dish content or is just a category
    index one hop away from it (confirmed against a real restaurant
    site's menu root having zero dish/calorie content at all) — so
    supplying nutrition_url must never skip menu-page discovery.
  - capture required source material: HTML via httpx, PDFs via httpx
    (content-type based), and a Playwright-rendered HTML capture only
    when the root page's raw HTML looks suspiciously thin (likely a
    client-side-rendered app shell) — browser automation stays an
    explicit fallback, not the default path. The fallback fetches
    *rendered* HTML (post-JS-execution), not a screenshot: a screenshot
    is an image multimodal_translation never reads (see that node's
    _read_text_materials — only HTML snapshots are sent to the AI), so a
    screenshot-only fallback would silently capture nothing usable for a
    JS-rendered page.
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

# A raw HTML page below this length is treated as likely client-side
# rendered (an empty app shell — script tags and a bare #root/#app div,
# no real content) and worth a browser-rendered fallback capture; a real
# menu-bearing page is essentially never this short even with heavy
# cookie-consent/analytics boilerplate. Confirmed against a real
# JS-rendered restaurant site (a bare React Native Web/Expo shell) that
# came in at ~3.4KB of raw HTML with zero menu content — comfortably
# under this threshold, whereas the old 2,000-byte threshold missed it.
_THIN_HTML_BYTES_THRESHOLD = 8_000


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

    async def fetch_rendered_html(self, *, source_id: uuid.UUID, url: str) -> PageCapture: ...

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
        return _Capture(snapshot=snapshot, html=await self._read_html_if_any(snapshot))

    async def fetch_rendered_html(self, *, source_id: uuid.UUID, url: str) -> PageCapture:
        snapshot, _metadata = await self._crawler.fetch_and_store(
            source_id=source_id, url=url, use_browser=True
        )
        return _Capture(snapshot=snapshot, html=await self._read_html_if_any(snapshot))

    async def fetch_screenshot(self, *, source_id: uuid.UUID, url: str) -> PageCapture:
        snapshot = await self._crawler.capture_screenshot(source_id=source_id, url=url)
        return _Capture(snapshot=snapshot, html=None)

    async def _read_html_if_any(self, snapshot: SourceSnapshot) -> str | None:
        if snapshot.content_type != SnapshotContentType.HTML:
            return None
        # fetch_and_store persists the bytes but doesn't hand the raw
        # HTML back (only parsed metadata); page discovery needs the
        # actual markup, so read the just-stored bytes back from storage
        # rather than re-fetching over the network.
        content = await self._storage.read(snapshot.storage_path)
        return content.decode("utf-8", errors="replace")


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
        nutrition_url: str | None = state.get("nutrition_url")
        fetcher = factory(source.url)

        try:
            snapshots = await _capture_source_material(
                fetcher, source=source, source_url=source_url, nutrition_url=nutrition_url
            )
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
    fetcher: PageFetcher, *, source: Source, source_url: str, nutrition_url: str | None = None
) -> list[SourceSnapshot]:
    """Fetches the source (menu) page itself, always runs deterministic
    link discovery from it to find menu-relevant linked pages (e.g. a
    chain whose menu root is a bare category index — burgers, breakfast,
    sides, etc. each on their own subpage — with zero dish-level content
    on the root page itself: confirmed against a real restaurant site
    where the root menu page had zero calorie/price mentions at all),
    and additionally fetches the caller-supplied nutrition page
    explicitly when one was given (manual ingestion — see
    CollectorState.nutrition_url).

    `nutrition_url` is deliberately just one more page to capture
    alongside whatever link discovery finds, not a shortcut that skips
    discovery — those are independent facts about a restaurant (whether
    its nutrition data lives at a caller-known URL vs. whether its menu
    content lives on the root page or one hop away), and treating
    "caller supplied a nutrition URL" as "therefore the menu page needs
    no further discovery" was what let the McDonald's-shaped case above
    silently reach multimodal_translation with a category index and no
    dishes to extract from any of them.

    Every fetch is captured and persisted as a SourceSnapshot; no
    content is interpreted here — only whether a page looks relevant
    enough to capture."""

    root_capture = await fetcher.fetch_html_or_pdf(source_id=source.id, url=source_url)
    snapshots = [root_capture.snapshot]
    # Which capture's HTML link discovery should run against below —
    # normally the raw fetch, but the rendered one once a thin-page
    # fallback replaces it (a client-side-rendered page's real nav links
    # often don't exist in the raw HTML at all, only post-render).
    discovery_capture = root_capture

    is_thin_html = (
        root_capture.snapshot.content_type == SnapshotContentType.HTML
        and root_capture.html is not None
        and len(root_capture.html.encode("utf-8")) < _THIN_HTML_BYTES_THRESHOLD
    )
    if is_thin_html:
        # Likely a client-side-rendered shell (e.g. a bare React/Expo
        # #root div with no server-rendered content); fall back to a
        # Playwright-rendered HTML capture of the same page — rendered
        # HTML, not a screenshot, since a screenshot is an image
        # multimodal_translation never reads (see that node's
        # _read_text_materials).
        rendered_capture = await fetcher.fetch_rendered_html(source_id=source.id, url=source_url)
        snapshots.append(rendered_capture.snapshot)
        if rendered_capture.html:
            discovery_capture = rendered_capture

    if nutrition_url is not None:
        domain_verifier = DomainVerifier(source.url)
        domain_verifier.assert_allowed(nutrition_url)
        nutrition_capture = await fetcher.fetch_html_or_pdf(source_id=source.id, url=nutrition_url)
        snapshots.append(nutrition_capture.snapshot)

    if discovery_capture.snapshot.content_type != SnapshotContentType.HTML or discovery_capture.html is None:
        # A PDF (or an HTML fetch whose body we couldn't read back) is
        # already the whole capture — there's no <head>/<a> structure to
        # run link discovery against.
        return snapshots

    domain_verifier = DomainVerifier(source.url)
    candidate_urls = find_menu_page_links(
        discovery_capture.html, base_url=source_url, domain_verifier=domain_verifier
    )

    for candidate_url in candidate_urls:
        if candidate_url == nutrition_url:
            continue  # already captured explicitly above
        capture = await fetcher.fetch_html_or_pdf(source_id=source.id, url=candidate_url)
        snapshots.append(capture.snapshot)

    return snapshots
