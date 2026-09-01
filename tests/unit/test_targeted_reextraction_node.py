"""Unit tests for the reviewer workflow's Targeted Re-Extraction node —
run against a real Postgres transaction (see tests/conftest.py) with a
fake PageFetcher (the exact same seam workflows/collector_workflow/
nodes/extraction.py defines) and a fake AIProvider, so behavior is
exercised through the actual node function without any real network,
browser, or OpenAI call.

Covers: source-material-only prompts (mirroring
tests/unit/test_multimodal_translation_node.py's coverage for the
collector workflow's equivalent boundary), mapping onto the *currently
published* restaurant rather than a blank one, and fail-closed paths.
"""

import uuid
from datetime import datetime, timezone

import pytest

from core.schemas.extraction_output import (
    ExtractedDish,
    ExtractedMenu,
    ExtractedMenuCategory,
    ExtractedRestaurantProfile,
    ExtractionOutput,
)
from core.schemas.restaurant import Restaurant, RestaurantLocation
from core.schemas.source import SnapshotContentType, Source, SourceSnapshot, SourceType
from infrastructure.ai.provider import AIProvider, AIProviderResult
from infrastructure.storage.base import StorageAdapter
from workflows.collector_workflow.nodes.extraction import PageFetcher
from workflows.reviewer_workflow.nodes.targeted_reextraction import build_targeted_reextraction_node

pytestmark = pytest.mark.asyncio


class _Capture:
    def __init__(self, snapshot: SourceSnapshot, html: str | None) -> None:
        self.snapshot = snapshot
        self.html = html


class FakePageFetcher(PageFetcher):
    def __init__(self, *, pages: dict[str, tuple[SnapshotContentType, str | None]], source_id: uuid.UUID) -> None:
        self._pages = pages
        self._source_id = source_id
        self.html_or_pdf_calls: list[str] = []

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
        content_type, html = self._pages[url]
        return _Capture(snapshot=self._make_snapshot(url, content_type), html=html)

    async def fetch_screenshot(self, *, source_id: uuid.UUID, url: str) -> _Capture:
        return _Capture(snapshot=self._make_snapshot(url, SnapshotContentType.SCREENSHOT), html=None)


class FakeStorageAdapter(StorageAdapter):
    def __init__(self, contents: dict[str, bytes]) -> None:
        self._contents = contents

    async def save(self, *, key: str, content: bytes) -> str:
        raise NotImplementedError

    async def read(self, storage_path: str) -> bytes:
        return self._contents[storage_path]


class FakeAIProvider(AIProvider):
    def __init__(self, *, output: ExtractionOutput) -> None:
        self._output = output
        self.calls: list[dict] = []

    async def generate_structured(self, *, system_prompt, user_content, response_model):
        self.calls.append({"system_prompt": system_prompt, "user_content": user_content})
        assert response_model is ExtractionOutput
        return AIProviderResult(output=self._output, model_name="fake-model-v1", overall_confidence=0.9)


def _restaurant() -> Restaurant:
    return Restaurant(
        name="Joe's Pizza",
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
    )


def _source(restaurant_id: uuid.UUID) -> Source:
    return Source(
        restaurant_id=restaurant_id,
        source_type=SourceType.RESTAURANT_WEBSITE,
        url="https://joes-pizza.com/",
        is_verified_domain=True,
    )


def _extraction_output() -> ExtractionOutput:
    return ExtractionOutput(
        restaurant_profile=ExtractedRestaurantProfile(description="Cozy pizzeria.", confidence=0.9),
        menus=[
            ExtractedMenu(
                categories=[
                    ExtractedMenuCategory(
                        name="Pizzas",
                        dishes=[ExtractedDish(name="Margherita", confidence=0.9)],
                    )
                ]
            )
        ],
    )


class TestSendsOnlySourceMaterial:
    async def test_user_content_excludes_restaurant_identity(self, db_session) -> None:
        restaurant = _restaurant()
        source = _source(restaurant.id)
        html = "<html><body>Margherita pizza $12.99</body></html>"
        fetcher = FakePageFetcher(pages={source.url: (SnapshotContentType.HTML, html)}, source_id=source.id)
        storage = FakeStorageAdapter({f"/fake/{source.url}": html.encode()})
        ai_provider = FakeAIProvider(output=_extraction_output())
        node = build_targeted_reextraction_node(
            db_session, storage, settings=None, ai_provider=ai_provider, page_fetcher_factory=lambda domain: fetcher
        )

        await node({"source": source, "restaurant": restaurant})

        assert "Joe's Pizza" not in ai_provider.calls[0]["user_content"]
        assert "Margherita pizza" in ai_provider.calls[0]["user_content"]


class TestMapsOntoCurrentRestaurant:
    async def test_untouched_fields_keep_the_currently_published_value(self, db_session) -> None:
        restaurant = _restaurant().model_copy(update={"logo_url": "https://joes-pizza.com/logo.png"})
        source = _source(restaurant.id)
        html = "<html><body>Margherita pizza $12.99</body></html>"
        fetcher = FakePageFetcher(pages={source.url: (SnapshotContentType.HTML, html)}, source_id=source.id)
        storage = FakeStorageAdapter({f"/fake/{source.url}": html.encode()})
        # AI output reports nothing for logo_url — the currently published
        # value must survive untouched, not be blanked out.
        ai_provider = FakeAIProvider(output=_extraction_output())
        node = build_targeted_reextraction_node(
            db_session, storage, settings=None, ai_provider=ai_provider, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node({"source": source, "restaurant": restaurant})

        assert update["reextracted_structured_json"]["logo_url"] == "https://joes-pizza.com/logo.png"

    async def test_menus_are_replaced_with_the_fresh_extraction(self, db_session) -> None:
        restaurant = _restaurant()
        source = _source(restaurant.id)
        html = "<html><body>Margherita pizza $12.99</body></html>"
        fetcher = FakePageFetcher(pages={source.url: (SnapshotContentType.HTML, html)}, source_id=source.id)
        storage = FakeStorageAdapter({f"/fake/{source.url}": html.encode()})
        ai_provider = FakeAIProvider(output=_extraction_output())
        node = build_targeted_reextraction_node(
            db_session, storage, settings=None, ai_provider=ai_provider, page_fetcher_factory=lambda domain: fetcher
        )

        update = await node({"source": source, "restaurant": restaurant})

        dish_names = [
            dish["name"]
            for menu in update["reextracted_structured_json"]["menus"]
            for category in menu["categories"]
            for dish in category["dishes"]
        ]
        assert dish_names == ["Margherita"]


class TestFailsClosedWithoutInput:
    async def test_missing_source_reports_an_error(self, db_session) -> None:
        node = build_targeted_reextraction_node(
            db_session, storage=None, settings=None, ai_provider=FakeAIProvider(output=_extraction_output())
        )

        update = await node({"restaurant": _restaurant()})

        assert "reextracted_structured_json" not in update
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "targeted_reextraction"
