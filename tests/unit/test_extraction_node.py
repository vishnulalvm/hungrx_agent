"""Unit tests for the collector workflow's Extraction node (Agent 2) —
run against a real Postgres transaction (see tests/conftest.py) with a
fake PageFetcher, so behavior is exercised through the actual node
function without any real network or browser calls.

Covers: HTML flow (link discovery + fetching candidates), PDF flow (no
link discovery), thin-HTML rendered-HTML fallback, snapshot persistence
references returned to the graph, and failure handling (missing source,
fetch errors) — never AI interpretation, which is explicitly out of
scope for this node.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from core.schemas.agent_run import AgentWorkflowType
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.source import SnapshotContentType, Source, SourceSnapshot, SourceType
from database.models.agent_run import AgentRun
from database.models.audit_log import AuditLog
from database.repositories.agent_run_repository import AgentRunRepository
from workflows.collector_workflow.nodes.extraction import PageFetcher, build_extraction_node

pytestmark = pytest.mark.asyncio


class _Capture:
    def __init__(self, snapshot: SourceSnapshot, html: str | None) -> None:
        self.snapshot = snapshot
        self.html = html


class FakePageFetcher(PageFetcher):
    """Records every fetch it's asked to perform and returns a
    deterministic, pre-configured snapshot per URL — no network, no
    filesystem, no browser."""

    def __init__(
        self,
        *,
        pages: dict[str, tuple[SnapshotContentType, str | None]],
        source_id: uuid.UUID,
        fail_on: set[str] | None = None,
        rendered_pages: dict[str, str] | None = None,
    ) -> None:
        self._pages = pages
        self._source_id = source_id
        self._fail_on = fail_on or set()
        self._rendered_pages = rendered_pages or {}
        self.html_or_pdf_calls: list[str] = []
        self.screenshot_calls: list[str] = []
        self.rendered_html_calls: list[str] = []

    def _make_snapshot(self, url: str, content_type: SnapshotContentType) -> SourceSnapshot:
        return SourceSnapshot(
            source_id=self._source_id,
            content_type=content_type,
            content_hash="a" * 64,
            storage_path=f"/fake/{url}",
            fetched_at=datetime.now(timezone.utc),
            http_status=200,
            content_length_bytes=100,
        )

    async def fetch_html_or_pdf(self, *, source_id: uuid.UUID, url: str) -> _Capture:
        self.html_or_pdf_calls.append(url)
        if url in self._fail_on:
            raise RuntimeError(f"simulated fetch failure for {url}")
        content_type, html = self._pages[url]
        return _Capture(snapshot=self._make_snapshot(url, content_type), html=html)

    async def fetch_rendered_html(self, *, source_id: uuid.UUID, url: str) -> _Capture:
        self.rendered_html_calls.append(url)
        html = self._rendered_pages.get(url)
        return _Capture(snapshot=self._make_snapshot(url, SnapshotContentType.HTML), html=html)

    async def fetch_screenshot(self, *, source_id: uuid.UUID, url: str) -> _Capture:
        self.screenshot_calls.append(url)
        return _Capture(snapshot=self._make_snapshot(url, SnapshotContentType.SCREENSHOT), html=None)


def _source(restaurant_id: uuid.UUID | None = None) -> Source:
    return Source(
        restaurant_id=restaurant_id or uuid.uuid4(),
        source_type=SourceType.RESTAURANT_WEBSITE,
        url="https://joes-pizza.com/",
        is_verified_domain=True,
    )


_RICH_HTML = (
    "<html><head><title>Joe's Pizza</title></head><body>"
    # Comfortably over _THIN_HTML_BYTES_THRESHOLD (8,000 bytes) — real
    # server-rendered menu pages are essentially always this size or
    # larger even before counting actual menu content.
    + "<p>" + ("Welcome to Joe's Pizza. " * 500) + "</p>"
    + '<a href="/menu">Our Menu</a>'
    + '<a href="/nutrition">Nutrition Info</a>'
    + '<a href="/about">About Us</a>'
    + "</body></html>"
)


class TestHtmlFlow:
    async def test_captures_root_page_and_discovered_menu_pages(self, db_session) -> None:
        source = _source()
        fetcher = FakePageFetcher(
            pages={
                "https://joes-pizza.com/": (SnapshotContentType.HTML, _RICH_HTML),
                "https://joes-pizza.com/menu": (SnapshotContentType.HTML, "<html>menu</html>"),
                "https://joes-pizza.com/nutrition": (SnapshotContentType.HTML, "<html>nutrition</html>"),
            },
            source_id=source.id,
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node({"source": source, "source_url": "https://joes-pizza.com/"})

        assert "errors" not in update
        assert len(update["source_snapshots"]) == 3
        assert update["source_snapshot"] == update["source_snapshots"][0]
        assert fetcher.html_or_pdf_calls == [
            "https://joes-pizza.com/",
            "https://joes-pizza.com/menu",
            "https://joes-pizza.com/nutrition",
        ]

    async def test_does_not_fetch_unrelated_links(self, db_session) -> None:
        source = _source()
        fetcher = FakePageFetcher(
            pages={"https://joes-pizza.com/": (SnapshotContentType.HTML, _RICH_HTML)},
            source_id=source.id,
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        await node({"source": source, "source_url": "https://joes-pizza.com/"})

        assert "https://joes-pizza.com/about" not in fetcher.html_or_pdf_calls


class TestPdfFlow:
    async def test_pdf_source_is_captured_with_no_link_discovery(self, db_session) -> None:
        source = _source()
        fetcher = FakePageFetcher(
            pages={"https://joes-pizza.com/menu.pdf": (SnapshotContentType.PDF, None)},
            source_id=source.id,
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node({"source": source, "source_url": "https://joes-pizza.com/menu.pdf"})

        assert len(update["source_snapshots"]) == 1
        assert update["source_snapshots"][0].content_type == SnapshotContentType.PDF
        assert fetcher.html_or_pdf_calls == ["https://joes-pizza.com/menu.pdf"]
        assert fetcher.screenshot_calls == []


class TestThinHtmlRenderedFallback:
    """A thin raw-HTML root capture (likely a client-side-rendered app
    shell) falls back to a Playwright-*rendered* HTML capture, never a
    screenshot — multimodal_translation only ever reads HTML snapshots
    (see that node's _read_text_materials), so a screenshot-only
    fallback would silently capture nothing the AI could use."""

    async def test_thin_html_triggers_a_rendered_html_capture_not_a_screenshot(self, db_session) -> None:
        # The rendered capture's HTML is also what link discovery runs
        # against (the raw/thin HTML has no real <a> tags at all in a
        # client-side-rendered page) — so this fixture's rendered HTML
        # links to /menu, and the fake must be able to serve that page
        # too for the node to succeed end-to-end.
        source = _source()
        thin_html = "<html><body><div id='root'></div></body></html>"
        rendered_html = (
            "<html><body><div id='root'><h1>Joe's Pizza</h1>"
            '<a href="/menu">Menu</a></div></body></html>'
        )
        fetcher = FakePageFetcher(
            pages={
                "https://joes-pizza.com/": (SnapshotContentType.HTML, thin_html),
                "https://joes-pizza.com/menu": (SnapshotContentType.HTML, "<html>menu</html>"),
            },
            source_id=source.id,
            rendered_pages={"https://joes-pizza.com/": rendered_html},
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node({"source": source, "source_url": "https://joes-pizza.com/"})

        assert "errors" not in update
        assert fetcher.rendered_html_calls == ["https://joes-pizza.com/"]
        assert fetcher.screenshot_calls == []
        # Discovery ran against the *rendered* HTML, not the thin raw
        # HTML — proven by /menu (only linked from rendered_html) having
        # actually been fetched.
        assert "https://joes-pizza.com/menu" in fetcher.html_or_pdf_calls
        content_types = [snap.content_type for snap in update["source_snapshots"]]
        assert content_types.count(SnapshotContentType.HTML) == 3

    async def test_rich_html_does_not_trigger_the_rendered_html_fallback(self, db_session) -> None:
        source = _source()
        fetcher = FakePageFetcher(
            pages={"https://joes-pizza.com/": (SnapshotContentType.HTML, _RICH_HTML)},
            source_id=source.id,
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        await node({"source": source, "source_url": "https://joes-pizza.com/"})

        assert fetcher.rendered_html_calls == []
        assert fetcher.screenshot_calls == []


class TestReturnsSourceReferencesOnly:
    async def test_state_update_contains_no_raw_content_keys(self, db_session) -> None:
        source = _source()
        fetcher = FakePageFetcher(
            pages={
                "https://joes-pizza.com/": (SnapshotContentType.HTML, _RICH_HTML),
                "https://joes-pizza.com/menu": (SnapshotContentType.HTML, "<html>menu</html>"),
                "https://joes-pizza.com/nutrition": (SnapshotContentType.HTML, "<html>nutrition</html>"),
            },
            source_id=source.id,
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node({"source": source, "source_url": "https://joes-pizza.com/"})

        assert set(update.keys()) <= {"source_snapshot", "source_snapshots", "errors"}
        for snapshot in update["source_snapshots"]:
            assert isinstance(snapshot, SourceSnapshot)


class TestExplicitNutritionUrl:
    async def test_fetches_nutrition_url_explicitly_alongside_discovered_menu_links(self, db_session) -> None:
        # nutrition_url is an *additional* explicit fetch, not a shortcut
        # that skips menu-page link discovery — a caller knowing the
        # nutrition page's URL says nothing about whether the menu root
        # itself contains real dish content or is just a category index
        # (see extraction.py's module docstring for the real-world case
        # this guards against: a chain whose menu root had zero dish
        # content, with everything one hop away via link discovery).
        source = _source()
        fetcher = FakePageFetcher(
            pages={
                "https://joes-pizza.com/menu": (SnapshotContentType.HTML, _RICH_HTML),
                "https://joes-pizza.com/nutrition-facts": (SnapshotContentType.HTML, "<html>nutrition</html>"),
                # Discovered from _RICH_HTML's own <a href="/menu">/
                # <a href="/nutrition"> links (distinct from the explicit
                # nutrition_url below) — proves discovery actually ran
                # rather than being skipped. (/about is not menu-keyword
                # matching, so find_menu_page_links correctly excludes
                # it — not part of what this test checks.)
                "https://joes-pizza.com/nutrition": (SnapshotContentType.HTML, "<html>discovered</html>"),
            },
            source_id=source.id,
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node(
            {
                "source": source,
                "source_url": "https://joes-pizza.com/menu",
                "nutrition_url": "https://joes-pizza.com/nutrition-facts",
            }
        )

        assert "errors" not in update
        # The explicit nutrition_url was fetched...
        assert "https://joes-pizza.com/nutrition-facts" in fetcher.html_or_pdf_calls
        # ...and link discovery still ran against the menu root and
        # fetched the distinct /nutrition link it found there too —
        # proof discovery wasn't skipped just because nutrition_url was
        # supplied.
        assert "https://joes-pizza.com/nutrition" in fetcher.html_or_pdf_calls
        assert fetcher.html_or_pdf_calls.count("https://joes-pizza.com/nutrition-facts") == 1

    async def test_nutrition_url_not_duplicated_when_also_discovered(self, db_session) -> None:
        # find_menu_page_links would discover the same URL nutrition_url
        # already names (a real link on the menu page pointing at the
        # exact nutrition page the caller supplied) — must be fetched
        # only once, not twice.
        source = _source()
        html_linking_to_nutrition = _RICH_HTML.replace(
            '<a href="/nutrition">Nutrition Info</a>', '<a href="/nutrition-facts">Nutrition Info</a>'
        )
        fetcher = FakePageFetcher(
            pages={
                "https://joes-pizza.com/menu": (SnapshotContentType.HTML, html_linking_to_nutrition),
                "https://joes-pizza.com/nutrition-facts": (SnapshotContentType.HTML, "<html>nutrition</html>"),
                "https://joes-pizza.com/about": (SnapshotContentType.HTML, "<html>about</html>"),
            },
            source_id=source.id,
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node(
            {
                "source": source,
                "source_url": "https://joes-pizza.com/menu",
                "nutrition_url": "https://joes-pizza.com/nutrition-facts",
            }
        )

        assert "errors" not in update
        assert fetcher.html_or_pdf_calls.count("https://joes-pizza.com/nutrition-facts") == 1

    async def test_rejects_nutrition_url_outside_verified_domain(self, db_session) -> None:
        source = _source()
        fetcher = FakePageFetcher(
            pages={"https://joes-pizza.com/menu": (SnapshotContentType.HTML, _RICH_HTML)},
            source_id=source.id,
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node(
            {
                "source": source,
                "source_url": "https://joes-pizza.com/menu",
                "nutrition_url": "https://evil.example.com/nutrition",
            }
        )

        assert len(update["errors"]) == 1
        assert "evil.example.com" not in fetcher.html_or_pdf_calls


class TestFailsClosedWithoutVerifiedSource:
    async def test_missing_source_on_state_reports_an_error(self, db_session) -> None:
        node = build_extraction_node(db_session, storage=None, settings=None)

        update = await node({})

        assert "source_snapshot" not in update
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "extraction"

    async def test_missing_source_url_reports_an_error(self, db_session) -> None:
        source = _source()
        node = build_extraction_node(db_session, storage=None, settings=None)

        update = await node({"source": source})

        assert "source_snapshot" not in update
        assert len(update["errors"]) == 1


class TestLogsFailures:
    async def test_fetch_failure_reports_an_error_without_raising(self, db_session) -> None:
        source = _source()
        fetcher = FakePageFetcher(
            pages={}, source_id=source.id, fail_on={"https://joes-pizza.com/"}
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node({"source": source, "source_url": "https://joes-pizza.com/"})

        assert "source_snapshot" not in update
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "extraction"

    async def test_fetch_failure_writes_an_audit_row_when_agent_run_id_present(self, db_session) -> None:
        source = _source()
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=source.restaurant_id
        )
        fetcher = FakePageFetcher(
            pages={}, source_id=source.id, fail_on={"https://joes-pizza.com/"}
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        await node(
            {
                "source": source,
                "source_url": "https://joes-pizza.com/",
                "agent_run_id": str(run.id),
            }
        )

        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.AGENT_RUN, AuditLog.entity_id == str(run.id)
            )
        )
        entry = rows.scalar_one()
        assert entry.action == AuditAction.AGENT_RUN_TRIGGER
        assert entry.metadata_["node"] == "extraction"

        run_row = await db_session.get(AgentRun, run.id)
        assert run_row.error_message is not None
