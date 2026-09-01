"""Unit tests for the collector workflow's Extraction node (Agent 2) —
run against a real Postgres transaction (see tests/conftest.py) with a
fake PageFetcher, so behavior is exercised through the actual node
function without any real network or browser calls.

Covers: HTML flow (link discovery + fetching candidates), PDF flow (no
link discovery), thin-HTML screenshot fallback, snapshot persistence
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
    ) -> None:
        self._pages = pages
        self._source_id = source_id
        self._fail_on = fail_on or set()
        self.html_or_pdf_calls: list[str] = []
        self.screenshot_calls: list[str] = []

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
    + "<p>" + ("Welcome to Joe's Pizza. " * 200) + "</p>"
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


class TestThinHtmlScreenshotFallback:
    async def test_thin_html_triggers_a_screenshot_capture(self, db_session) -> None:
        source = _source()
        thin_html = "<html><body>Loading...</body></html>"
        fetcher = FakePageFetcher(
            pages={"https://joes-pizza.com/": (SnapshotContentType.HTML, thin_html)},
            source_id=source.id,
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node({"source": source, "source_url": "https://joes-pizza.com/"})

        assert fetcher.screenshot_calls == ["https://joes-pizza.com/"]
        content_types = {snap.content_type for snap in update["source_snapshots"]}
        assert SnapshotContentType.SCREENSHOT in content_types

    async def test_rich_html_does_not_trigger_a_screenshot(self, db_session) -> None:
        source = _source()
        fetcher = FakePageFetcher(
            pages={"https://joes-pizza.com/": (SnapshotContentType.HTML, _RICH_HTML)},
            source_id=source.id,
        )
        node = build_extraction_node(
            db_session, storage=None, settings=None, page_fetcher_factory=lambda domain: fetcher
        )

        await node({"source": source, "source_url": "https://joes-pizza.com/"})

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
