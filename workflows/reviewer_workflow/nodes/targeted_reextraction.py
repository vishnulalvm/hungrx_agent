"""Targeted Re-Extraction node (Reviewer Workflow, stage 2): only reached
when Temporal Hash Polling found `hash_changed == True`. Re-runs the same
capture + AI-structuring path the collector workflow's Extraction and
Multimodal Translation nodes use, scoped to the one source that's known
to have changed — "targeted" as opposed to a full from-scratch crawl of
every page the collector workflow originally discovered, since a
temporal re-check only needs to re-read what's actually live now, not
rediscover the site's structure again.

Responsibilities:
  - re-fetch the source root page (HTML/PDF) plus any menu/nutrition
    pages discoverable from it, same deterministic link discovery
    (infrastructure.crawler.page_discovery) the collector workflow's
    Extraction node uses — not a different, ad hoc capture path
  - persist every fetch as a SourceSnapshot, same as Extraction
  - send only that freshly captured material to the AI provider via
    strict structured output (AIProvider.generate_structured with
    response_model=ExtractionOutput) — the same strict-schema boundary
    Multimodal Translation enforces; no restaurant identity or database
    context beyond the raw page content is ever included in the prompt
  - map the AI's ExtractionOutput onto the *currently published*
    Restaurant (state["restaurant"]) rather than a blank one, so fields
    the fresh crawl didn't re-report (menus untouched by whatever
    changed, restaurant profile fields the model found nothing new for)
    fall back to what's already live, not to empty/default values
  - never write to a restaurant/menu/dish repository directly — this
    node only ever touches AgentRun/AuditLog, identical to the collector
    workflow's Multimodal Translation boundary
"""

import logging
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.config.settings import Settings
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.extraction_output import ExtractedDish, ExtractedMenuCategory, ExtractionOutput
from core.schemas.menu import Dish, Ingredient, Menu, MenuCategory
from core.schemas.nutrition import Nutrition
from core.schemas.restaurant import Restaurant
from core.schemas.source import SnapshotContentType, Source, SourceSnapshot
from database.repositories.agent_run_repository import AgentRunRepository
from infrastructure.ai.provider import AIProvider, AIProviderError
from infrastructure.crawler.domain_lock import DomainVerifier
from infrastructure.crawler.page_discovery import find_menu_page_links
from infrastructure.storage.base import StorageAdapter
from workflows.collector_workflow.nodes.extraction import CrawlerServicePageFetcher, PageFetcher
from workflows.reviewer_workflow.state import ReviewerState

logger = logging.getLogger("hungrx.workflows.reviewer.targeted_reextraction")

NODE_NAME = "targeted_reextraction"

TargetedReextractionNode = Callable[[ReviewerState], Awaitable[dict[str, Any]]]

_THIN_HTML_BYTES_THRESHOLD = 2_000

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

_MAX_SNAPSHOT_CHARS = 20_000


def build_targeted_reextraction_node(
    session: AsyncSession,
    storage: StorageAdapter,
    settings: Settings,
    ai_provider: AIProvider,
    *,
    page_fetcher_factory: Callable[[str], PageFetcher] | None = None,
) -> TargetedReextractionNode:
    """`page_fetcher_factory(verified_domain) -> PageFetcher` reuses the
    exact same seam workflows/collector_workflow/nodes/extraction.py
    defines — production defaults to a real CrawlerService-backed
    fetcher; tests pass a fake factory to avoid any real network call."""
    audit = AuditService(session)
    agent_runs = AgentRunRepository(session)
    factory = page_fetcher_factory or (
        lambda domain: CrawlerServicePageFetcher(verified_domain=domain, storage=storage, settings=settings)
    )

    async def targeted_reextraction_node(state: ReviewerState) -> dict[str, Any]:
        source: Source | None = state.get("source")
        restaurant: Restaurant | None = state.get("restaurant")
        run_id = state.get("agent_run_id")

        if source is None or restaurant is None:
            message = "ReviewerState.source/restaurant are required before targeted_reextraction runs"
            logger.error("targeted_reextraction node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        fetcher = factory(source.url)

        try:
            snapshots = await _capture_source_material(fetcher, source=source)
        except Exception as exc:  # crawler/storage failures must not crash the graph run
            failure_message = f"targeted_reextraction failed to capture source material for source_id={source.id}: {exc}"
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
            failure_message = f"targeted_reextraction captured no snapshots for source_id={source.id}"
            logger.warning(failure_message)
            return {"errors": [{"node": NODE_NAME, "message": failure_message}]}

        materials = await _read_text_materials(storage, snapshots)
        if not materials:
            failure_message = "targeted_reextraction found no text-readable material to send to the AI provider"
            logger.warning(failure_message)
            return {"errors": [{"node": NODE_NAME, "message": failure_message}]}

        try:
            result = await ai_provider.generate_structured(
                system_prompt=_SYSTEM_PROMPT,
                user_content=_build_user_content(materials),
                response_model=ExtractionOutput,
            )
        except AIProviderError as exc:
            failure_message = f"targeted_reextraction AI call failed for restaurant_id={restaurant.id}: {exc}"
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

        mapped_restaurant = _map_onto_current(result.output, current=restaurant)

        if run_id is not None:
            await audit.log(
                action=AuditAction.AI_EXTRACTION,
                entity_type=AuditEntityType.AGENT_RUN,
                entity_id=run_id,
                metadata={
                    "node": NODE_NAME,
                    "restaurant_id": str(restaurant.id),
                    "model_name": result.model_name,
                    "source_id": str(source.id),
                },
            )

        return {
            "reextraction_snapshots": snapshots,
            "reextracted_structured_json": mapped_restaurant.model_dump(mode="json"),
        }

    return targeted_reextraction_node


async def _capture_source_material(fetcher: PageFetcher, *, source: Source) -> list[SourceSnapshot]:
    """Same capture strategy as the collector workflow's Extraction node
    (root page, thin-HTML screenshot fallback, deterministic menu-link
    discovery) — kept identical rather than reinvented, since "targeted"
    describes the scope (one known-changed source), not a different
    capture algorithm."""
    root_capture = await fetcher.fetch_html_or_pdf(source_id=source.id, url=source.url)
    snapshots = [root_capture.snapshot]

    if root_capture.snapshot.content_type != SnapshotContentType.HTML or root_capture.html is None:
        return snapshots

    if len(root_capture.html.encode("utf-8")) < _THIN_HTML_BYTES_THRESHOLD:
        screenshot_capture = await fetcher.fetch_screenshot(source_id=source.id, url=source.url)
        snapshots.append(screenshot_capture.snapshot)

    domain_verifier = DomainVerifier(source.url)
    candidate_urls = find_menu_page_links(root_capture.html, base_url=source.url, domain_verifier=domain_verifier)

    for candidate_url in candidate_urls:
        capture = await fetcher.fetch_html_or_pdf(source_id=source.id, url=candidate_url)
        snapshots.append(capture.snapshot)

    return snapshots


async def _read_text_materials(
    storage: StorageAdapter, snapshots: list[SourceSnapshot]
) -> list[tuple[SourceSnapshot, str]]:
    materials: list[tuple[SourceSnapshot, str]] = []
    for snapshot in snapshots:
        if snapshot.content_type != SnapshotContentType.HTML:
            continue
        content = await storage.read(snapshot.storage_path)
        materials.append((snapshot, content.decode("utf-8", errors="replace")))
    return materials


def _build_user_content(materials: list[tuple[SourceSnapshot, str]]) -> str:
    sections = []
    for snapshot, text in materials:
        truncated = text[:_MAX_SNAPSHOT_CHARS]
        sections.append(f"[snapshot:{snapshot.id}]\n{truncated}")
    return "\n\n---\n\n".join(sections)


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


def _map_onto_current(output: ExtractionOutput, *, current: Restaurant) -> Restaurant:
    """Merges a fresh ExtractionOutput onto the *currently published*
    Restaurant, not a blank one — unlike the collector workflow's first
    pass (nothing published yet), a reviewer run's whole point is
    checking an already-live restaurant for drift, so anything the model
    reports replaces the live value and anything it reports nothing new
    for keeps what's already published."""
    profile = output.restaurant_profile
    return current.model_copy(
        update={
            "description": profile.description or current.description,
            "cuisine_types": profile.cuisine_types or current.cuisine_types,
            "logo_url": profile.logo_url or current.logo_url,
            "cover_image_url": profile.cover_image_url or current.cover_image_url,
            "menus": [
                Menu(name=menu.name, categories=[_map_category(cat) for cat in menu.categories])
                for menu in output.menus
            ]
            or current.menus,
        }
    )
