"""Multimodal Translation node (Collector Workflow Agent 3): turns the
raw source material Extraction captured into `structured_json` — a
strictly typed shape validated against `core.schemas.extraction_output`
and mapped into `core.schemas.restaurant`/`menu`/`nutrition` domain
objects the rest of the pipeline understands.

Responsibilities (per the collector workflow's Agent 3 spec):
  - send only collected source material to the model: the user-content
    sent to AIProvider is built exclusively from state["source_snapshots"]
    (via StorageAdapter) — no restaurant identity, no database rows, no
    unrelated context is ever included
  - strict structured output: AIProvider.generate_structured is called
    with response_model=ExtractionOutput, OpenAI's json_schema strict
    mode (see infrastructure/ai/openai_provider.py) — the model cannot
    return anything outside that schema
  - map content into our Pydantic schemas: ExtractionOutput (AI-only,
    no ids) is translated into real core.schemas.restaurant/menu/nutrition
    objects in Python, with ids assigned here, not by the model
  - do not allow free-form output: enforced at the API level by
    response_format, not by prompt instruction alone
  - include source references: every ExtractedDish/RestaurantProfile
    carries source_snapshot_ids, preserved through translation
  - confidence metadata: per-dish/profile confidence from the model is
    preserved on structured_json rather than being summarized away
  - never modify the database directly: this node only ever writes to
    AgentRun/AuditLog (same as Source Authority/Extraction) — it has no
    access to any restaurant/menu/dish repository, and structured_json is
    just a state field for downstream nodes (Deterministic Validation,
    Human Review) to act on, not a DB write

A LangGraph node function's signature is fixed to `(state) -> partial
state update`; `build_multimodal_translation_node` is a factory (same
pattern as source_authority/extraction) closing over the DB session,
storage adapter, and AIProvider.
"""

import logging
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.extraction_output import (
    ExtractedDish,
    ExtractedMenuCategory,
    ExtractionOutput,
)
from core.schemas.menu import Dish, Ingredient, Menu, MenuCategory
from core.schemas.nutrition import Nutrition
from core.schemas.restaurant import Restaurant
from core.schemas.source import SnapshotContentType, SourceSnapshot
from database.repositories.agent_run_repository import AgentRunRepository
from infrastructure.ai.provider import AIProvider, AIProviderError
from infrastructure.storage.base import StorageAdapter
from workflows.collector_workflow.state import CollectorState

logger = logging.getLogger("hungrx.workflows.collector.multimodal_translation")

NODE_NAME = "multimodal_translation"

MultimodalTranslationNode = Callable[[CollectorState], Awaitable[dict[str, Any]]]

_SYSTEM_PROMPT = (
    "You extract restaurant menu and nutrition data from raw crawled "
    "website/PDF content. Only report information that is actually "
    "present in the provided material — never infer, guess, or fill in "
    "plausible-sounding values. Leave a field unset (null/empty) rather "
    "than fabricate it. For every dish and for the restaurant profile, "
    "report which source documents (by the [snapshot:<id>] markers in "
    "the material) the information came from, and a confidence score "
    "between 0 and 1 reflecting how clearly the material supports the "
    "extracted values."
)

# Keeps a single call's prompt bounded — a very large crawl (many
# discovered pages) must not silently balloon token usage/cost per run.
_MAX_SNAPSHOT_CHARS = 20_000


def _build_user_content(materials: list[tuple[SourceSnapshot, str]]) -> str:
    """Builds the model's user message from ONLY the collected source
    material — no restaurant name, no database identifiers beyond the
    snapshot id markers used for source-reference attribution."""
    sections = []
    for snapshot, text in materials:
        truncated = text[:_MAX_SNAPSHOT_CHARS]
        sections.append(f"[snapshot:{snapshot.id}]\n{truncated}")
    return "\n\n---\n\n".join(sections)


async def _read_text_materials(
    storage: StorageAdapter, snapshots: list[SourceSnapshot]
) -> list[tuple[SourceSnapshot, str]]:
    """Reads back HTML snapshot content as text. PDFs/screenshots are
    skipped here (no text to extract without a dedicated PDF/OCR
    pipeline, which is out of scope for this node) — they still exist as
    SourceSnapshot references on state for a future node to use, but
    this node only sends the model material it can actually turn into
    text."""
    materials: list[tuple[SourceSnapshot, str]] = []
    for snapshot in snapshots:
        if snapshot.content_type != SnapshotContentType.HTML:
            continue
        content = await storage.read(snapshot.storage_path)
        materials.append((snapshot, content.decode("utf-8", errors="replace")))
    return materials


def _map_dish(extracted: ExtractedDish, *, category_id: uuid.UUID) -> Dish:
    return Dish(
        category_id=category_id,
        name=extracted.name,
        description=extracted.description,
        image_url=extracted.image_url,
        nutrition=Nutrition(
            serving_size=extracted.nutrition.serving_size,
            macros=extracted.nutrition.macros,
            micronutrients=extracted.nutrition.micronutrients,
        ),
        allergens=extracted.allergens,
        ingredients=[Ingredient(name=name) for name in extracted.ingredient_names if name.strip()],
        quantity=extracted.quantity,
        price=extracted.price,
        currency=extracted.currency or "USD",
    )


def _map_category(extracted: ExtractedMenuCategory) -> MenuCategory:
    category = MenuCategory(name=extracted.name)
    category.dishes = [_map_dish(dish, category_id=category.id) for dish in extracted.dishes]
    return category


def _map_to_restaurant(output: ExtractionOutput, *, base_restaurant: Restaurant) -> Restaurant:
    """Merges the AI's ExtractionOutput onto the caller-known Restaurant
    (identity/location fields untouched — the model was never asked for
    those) to produce the full Restaurant object structured_json holds.
    """
    profile = output.restaurant_profile
    return base_restaurant.model_copy(
        update={
            "description": profile.description or base_restaurant.description,
            "cuisine_types": profile.cuisine_types or base_restaurant.cuisine_types,
            "logo_url": profile.logo_url or base_restaurant.logo_url,
            "cover_image_url": profile.cover_image_url or base_restaurant.cover_image_url,
            "menus": [
                Menu(name=menu.name, categories=[_map_category(cat) for cat in menu.categories])
                for menu in output.menus
            ],
        }
    )


def _extraction_metadata(output: ExtractionOutput) -> dict[str, Any]:
    """Confidence/source-reference metadata pulled out of the mapped
    structured_json so downstream nodes (and reviewers) don't have to
    walk the whole restaurant tree to find it."""
    dish_confidences = [
        {"name": dish.name, "confidence": dish.confidence, "source_snapshot_ids": dish.source_snapshot_ids}
        for menu in output.menus
        for category in menu.categories
        for dish in category.dishes
    ]
    return {
        "restaurant_profile_confidence": output.restaurant_profile.confidence,
        "restaurant_profile_source_snapshot_ids": output.restaurant_profile.source_snapshot_ids,
        "dishes": dish_confidences,
    }


def build_multimodal_translation_node(
    session: AsyncSession, storage: StorageAdapter, ai_provider: AIProvider
) -> MultimodalTranslationNode:
    audit = AuditService(session)
    agent_runs = AgentRunRepository(session)

    async def multimodal_translation_node(state: CollectorState) -> dict[str, Any]:
        restaurant: Restaurant | None = state.get("restaurant")
        snapshots: list[SourceSnapshot] = state.get("source_snapshots") or []

        if restaurant is None or not snapshots:
            message = (
                "CollectorState.restaurant/source_snapshots are required before the "
                "multimodal_translation node runs (extraction must succeed first)"
            )
            logger.error("multimodal_translation node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        run_id = state.get("agent_run_id")

        try:
            materials = await _read_text_materials(storage, snapshots)
            if not materials:
                raise AIProviderError("no text-readable source material available to translate")

            user_content = _build_user_content(materials)
            result = await ai_provider.generate_structured(
                system_prompt=_SYSTEM_PROMPT,
                user_content=user_content,
                response_model=ExtractionOutput,
            )
        except AIProviderError as exc:
            failure_message = f"multimodal_translation failed for restaurant_id={restaurant.id}: {exc}"
            logger.warning(failure_message)
            if run_id is not None:
                await audit.log(
                    action=AuditAction.AI_EXTRACTION,
                    entity_type=AuditEntityType.AGENT_RUN,
                    entity_id=run_id,
                    metadata={"node": NODE_NAME, "restaurant_id": str(restaurant.id), "error": str(exc)},
                )
                await agent_runs.mark_failed(uuid.UUID(run_id), error_message=failure_message)
            return {"errors": [{"node": NODE_NAME, "message": failure_message}]}

        mapped_restaurant = _map_to_restaurant(result.output, base_restaurant=restaurant)

        if run_id is not None:
            await audit.log(
                action=AuditAction.AI_EXTRACTION,
                entity_type=AuditEntityType.AGENT_RUN,
                entity_id=run_id,
                metadata={
                    "node": NODE_NAME,
                    "restaurant_id": str(restaurant.id),
                    "model_name": result.model_name,
                    **_extraction_metadata(result.output),
                },
            )

        extraction_result: dict[str, Any] = {
            "raw_payload": result.output.model_dump(mode="json"),
            "model_name": result.model_name,
        }
        if result.overall_confidence is not None:
            extraction_result["confidence"] = result.overall_confidence

        return {
            "extraction_result": extraction_result,
            "structured_json": mapped_restaurant.model_dump(mode="json"),
        }

    return multimodal_translation_node
