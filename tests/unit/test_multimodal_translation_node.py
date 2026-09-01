"""Unit tests for the collector workflow's Multimodal Translation node
(Agent 3) — run against a real Postgres transaction (see
tests/conftest.py) with a fake AIProvider returning mocked structured
model responses, so behavior is exercised through the actual node
function without any real OpenAI call.

Covers: sending only collected source material, strict structured
output/no free-form output, mapping into domain schemas, source
references, confidence metadata, and that the node never touches the
database beyond AgentRun/AuditLog (no restaurant/menu/dish repository is
even constructed here).
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from core.schemas.agent_run import AgentWorkflowType
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.extraction_output import (
    ExtractedDish,
    ExtractedMenu,
    ExtractedMenuCategory,
    ExtractedRestaurantProfile,
    ExtractionOutput,
)
from core.schemas.menu import Allergen
from core.schemas.restaurant import Restaurant, RestaurantLocation
from core.schemas.source import SnapshotContentType, SourceSnapshot
from database.models.agent_run import AgentRun
from database.models.audit_log import AuditLog
from database.repositories.agent_run_repository import AgentRunRepository
from infrastructure.ai.provider import AIProvider, AIProviderError, AIProviderResult
from infrastructure.storage.base import StorageAdapter
from workflows.collector_workflow.nodes.multimodal_translation import build_multimodal_translation_node

pytestmark = pytest.mark.asyncio


class FakeStorageAdapter(StorageAdapter):
    def __init__(self, contents: dict[str, bytes]) -> None:
        self._contents = contents

    async def save(self, *, key: str, content: bytes) -> str:
        raise NotImplementedError("not needed for these tests")

    async def read(self, storage_path: str) -> bytes:
        return self._contents[storage_path]


class FakeAIProvider(AIProvider):
    """Returns a pre-configured mocked structured response and records
    every call it's asked to make (system_prompt/user_content/
    response_model) so tests can assert exactly what was sent to the
    model — no real OpenAI call."""

    def __init__(self, *, output: ExtractionOutput | None = None, error: Exception | None = None) -> None:
        self._output = output
        self._error = error
        self.calls: list[dict] = []

    async def generate_structured(self, *, system_prompt, user_content, response_model):
        self.calls.append(
            {"system_prompt": system_prompt, "user_content": user_content, "response_model": response_model}
        )
        if self._error is not None:
            raise self._error
        assert response_model is ExtractionOutput
        return AIProviderResult(output=self._output, model_name="fake-model-v1", overall_confidence=0.9)


def _restaurant() -> Restaurant:
    return Restaurant(
        name="Joe's Pizza",
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
    )


def _snapshot(*, storage_path: str, content_type: SnapshotContentType = SnapshotContentType.HTML) -> SourceSnapshot:
    return SourceSnapshot(
        source_id=uuid.uuid4(),
        content_type=content_type,
        content_hash="a" * 64,
        storage_path=storage_path,
        fetched_at=datetime.now(timezone.utc),
        http_status=200,
        content_length_bytes=100,
    )


def _sample_output(*, snapshot_id: str) -> ExtractionOutput:
    return ExtractionOutput(
        restaurant_profile=ExtractedRestaurantProfile(
            description="A cozy neighborhood pizzeria.",
            cuisine_types=["Italian", "Pizza"],
            confidence=0.85,
            source_snapshot_ids=[snapshot_id],
        ),
        menus=[
            ExtractedMenu(
                name="Main Menu",
                categories=[
                    ExtractedMenuCategory(
                        name="Pizzas",
                        dishes=[
                            ExtractedDish(
                                name="Margherita",
                                description="Tomato, mozzarella, basil",
                                allergens=[Allergen.MILK, Allergen.WHEAT],
                                confidence=0.95,
                                source_snapshot_ids=[snapshot_id],
                            )
                        ],
                    )
                ],
            )
        ],
    )


class TestSendsOnlyCollectedSourceMaterial:
    async def test_user_content_contains_snapshot_html(self, db_session) -> None:
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>Margherita pizza $12</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        await node({"restaurant": _restaurant(), "source_snapshots": [snapshot]})

        assert len(provider.calls) == 1
        assert "Margherita pizza $12" in provider.calls[0]["user_content"]

    async def test_user_content_excludes_restaurant_identity_fields(self, db_session) -> None:
        # The restaurant's name/location must never appear in what's sent
        # to the model — only the collected source material.
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu content</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        await node({"restaurant": _restaurant(), "source_snapshots": [snapshot]})

        assert "Joe's Pizza" not in provider.calls[0]["user_content"]
        assert "Springfield" not in provider.calls[0]["user_content"]

    async def test_non_html_snapshots_are_not_sent_as_text(self, db_session) -> None:
        html_snap = _snapshot(storage_path="snap-html")
        pdf_snap = _snapshot(storage_path="snap-pdf", content_type=SnapshotContentType.PDF)
        storage = FakeStorageAdapter({"snap-html": b"<html>text content</html>", "snap-pdf": b"%PDF-1.4 binary"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(html_snap.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        await node({"restaurant": _restaurant(), "source_snapshots": [html_snap, pdf_snap]})

        assert "%PDF" not in provider.calls[0]["user_content"]


class TestStrictStructuredOutput:
    async def test_calls_provider_with_extraction_output_response_model(self, db_session) -> None:
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        await node({"restaurant": _restaurant(), "source_snapshots": [snapshot]})

        assert provider.calls[0]["response_model"] is ExtractionOutput

    async def test_provider_error_is_not_swallowed_into_fabricated_output(self, db_session) -> None:
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(error=AIProviderError("model refused to respond"))
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"restaurant": _restaurant(), "source_snapshots": [snapshot]})

        assert "structured_json" not in update
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "multimodal_translation"


class TestMapsIntoDomainSchemas:
    async def test_structured_json_contains_mapped_dish_fields(self, db_session) -> None:
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"restaurant": _restaurant(), "source_snapshots": [snapshot]})

        structured = update["structured_json"]
        dish = structured["menus"][0]["categories"][0]["dishes"][0]
        assert dish["name"] == "Margherita"
        assert dish["description"] == "Tomato, mozzarella, basil"
        assert set(dish["allergens"]) == {"milk", "wheat"}

    async def test_structured_json_preserves_restaurant_identity(self, db_session) -> None:
        # Identity fields (name, id, locations) come from the caller-known
        # Restaurant, not the model — the model was never asked for them.
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        restaurant = _restaurant()
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"restaurant": restaurant, "source_snapshots": [snapshot]})

        structured = update["structured_json"]
        assert structured["name"] == "Joe's Pizza"
        assert structured["id"] == str(restaurant.id)

    async def test_structured_json_applies_ai_reported_profile_fields(self, db_session) -> None:
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"restaurant": _restaurant(), "source_snapshots": [snapshot]})

        structured = update["structured_json"]
        assert structured["description"] == "A cozy neighborhood pizzeria."
        assert structured["cuisine_types"] == ["Italian", "Pizza"]

    async def test_model_can_never_return_extra_fields(self, db_session) -> None:
        # ExtractionOutput/ExtractedDish forbid extra keys at the schema
        # level — attempting to construct one with an unknown field
        # raises, proving free-form output cannot survive validation.
        with pytest.raises(Exception):
            ExtractedDish(name="Margherita", made_up_field="not allowed")


class TestIncludesSourceReferences:
    async def test_extraction_result_raw_payload_preserves_source_snapshot_ids(self, db_session) -> None:
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"restaurant": _restaurant(), "source_snapshots": [snapshot]})

        raw = update["extraction_result"]["raw_payload"]
        dish = raw["menus"][0]["categories"][0]["dishes"][0]
        assert dish["source_snapshot_ids"] == [str(snapshot.id)]
        assert raw["restaurant_profile"]["source_snapshot_ids"] == [str(snapshot.id)]


class TestReturnsConfidenceMetadata:
    async def test_extraction_result_includes_overall_confidence(self, db_session) -> None:
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"restaurant": _restaurant(), "source_snapshots": [snapshot]})

        assert update["extraction_result"]["confidence"] == 0.9

    async def test_raw_payload_preserves_per_dish_confidence(self, db_session) -> None:
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"restaurant": _restaurant(), "source_snapshots": [snapshot]})

        dish = update["extraction_result"]["raw_payload"]["menus"][0]["categories"][0]["dishes"][0]
        assert dish["confidence"] == 0.95


class TestNeverModifiesDatabaseDirectly:
    async def test_returns_state_update_only_no_restaurant_rows_written(self, db_session) -> None:
        # There is no restaurant/menu/dish table in this schema at all
        # (see database/models/__init__.py) — this test asserts the node
        # only ever produces a state dict; it cannot, structurally, write
        # restaurant data anywhere, since it never even imports a
        # restaurant repository.
        import workflows.collector_workflow.nodes.multimodal_translation as module

        source = "\n".join(
            line for line in open(module.__file__).readlines() if not line.strip().startswith("#")
        )
        assert "restaurant_repository" not in source
        assert "RestaurantRepository" not in source

    async def test_state_update_keys_are_limited_to_extraction_and_structured_json(self, db_session) -> None:
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"restaurant": _restaurant(), "source_snapshots": [snapshot]})

        assert set(update.keys()) <= {"extraction_result", "structured_json", "errors"}


class TestFailsClosedWithoutInput:
    async def test_missing_restaurant_reports_an_error(self, db_session) -> None:
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"source_snapshots": [snapshot]})

        assert "structured_json" not in update
        assert len(update["errors"]) == 1
        assert provider.calls == []

    async def test_missing_source_snapshots_reports_an_error(self, db_session) -> None:
        storage = FakeStorageAdapter({})
        provider = FakeAIProvider(output=_sample_output(snapshot_id="whatever"))
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"restaurant": _restaurant(), "source_snapshots": []})

        assert "structured_json" not in update
        assert len(update["errors"]) == 1
        assert provider.calls == []

    async def test_no_text_readable_snapshots_reports_an_error(self, db_session) -> None:
        pdf_snap = _snapshot(storage_path="snap-pdf", content_type=SnapshotContentType.PDF)
        storage = FakeStorageAdapter({"snap-pdf": b"%PDF binary"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(pdf_snap.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        update = await node({"restaurant": _restaurant(), "source_snapshots": [pdf_snap]})

        assert "structured_json" not in update
        assert len(update["errors"]) == 1
        assert provider.calls == []


class TestLogsFailuresAndAgentRun:
    async def test_failure_writes_audit_row_and_marks_agent_run_failed(self, db_session) -> None:
        restaurant = _restaurant()
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=restaurant.id
        )
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(error=AIProviderError("boom"))
        node = build_multimodal_translation_node(db_session, storage, provider)

        await node(
            {
                "restaurant": restaurant,
                "source_snapshots": [snapshot],
                "agent_run_id": str(run.id),
            }
        )

        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.AGENT_RUN, AuditLog.entity_id == str(run.id)
            )
        )
        entry = rows.scalar_one()
        assert entry.action == AuditAction.AI_EXTRACTION
        assert entry.metadata_["node"] == "multimodal_translation"

        run_row = await db_session.get(AgentRun, run.id)
        assert run_row.error_message is not None

    async def test_success_writes_an_ai_extraction_audit_row_with_model_metadata(self, db_session) -> None:
        restaurant = _restaurant()
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=restaurant.id
        )
        snapshot = _snapshot(storage_path="snap-1")
        storage = FakeStorageAdapter({"snap-1": b"<html>menu</html>"})
        provider = FakeAIProvider(output=_sample_output(snapshot_id=str(snapshot.id)))
        node = build_multimodal_translation_node(db_session, storage, provider)

        await node(
            {
                "restaurant": restaurant,
                "source_snapshots": [snapshot],
                "agent_run_id": str(run.id),
            }
        )

        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.AGENT_RUN, AuditLog.entity_id == str(run.id)
            )
        )
        entry = rows.scalar_one()
        assert entry.action == AuditAction.AI_EXTRACTION
        assert entry.metadata_["model_name"] == "fake-model-v1"
