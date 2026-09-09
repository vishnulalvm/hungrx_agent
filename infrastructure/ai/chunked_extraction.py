"""Two-phase structured extraction, shared by the collector workflow's
Multimodal Translation node and the reviewer workflow's Targeted
Re-Extraction node — both need "turn crawled source material into an
`ExtractionOutput`", and both hit the same underlying AIProvider limit,
so the chunking logic lives here once rather than being duplicated.

Why this exists at all: asking a model for the *entire*
`core.schemas.extraction_output.ExtractionOutput` shape in one
structured-output call (restaurant profile + every menu + every
category + every dish + full nutrition, all at once) reliably fails
against OpenAI's strict `json_schema` mode — confirmed against a real
account, the response comes back with zero completion tokens and
`finish_reason="length"`, no explanatory error. This isn't a token-
budget problem (raising `max_tokens` doesn't help) and isn't primarily a
schema-*size* problem either — it's specifically that this schema, once
assembled as one giant nested structure, is more than strict mode can
reliably produce.

The fix is to never build that one giant schema at all:

  Phase 1 (`ExtractedDiscovery`) — one call: restaurant profile plus a
  flat list of (menu, category) names found in the material. No dish
  detail. Small schema, always succeeds.

  Phase 2 (`ExtractedCategoryDishes`) — one call *per category* found in
  phase 1: only that category's dishes, with full nutrition detail. Each
  call's schema is small (a handful of `ExtractedDish` objects at most),
  so it stays well within what strict mode can produce.

  Assembly — phase 1's structure plus every phase 2 call's dishes are
  merged in Python into a real `ExtractionOutput`, which is what callers
  actually receive; nothing about the *shape* callers work with changes,
  only how it's obtained from the model.

If a restaurant's source material describes no categories at all (phase
1 returns an empty list), only the phase 1 call is made — there's
nothing to extract dishes for, so no phase 2 calls happen.
"""

import asyncio
import logging

from core.schemas.extraction_output import (
    ExtractedCategoryDishes,
    ExtractedDiscovery,
    ExtractedMenu,
    ExtractedMenuCategory,
    ExtractedMenuCategoryStub,
    ExtractionOutput,
)
from infrastructure.ai.provider import AIProvider, AIProviderError, AIProviderResult

logger = logging.getLogger("hungrx.infrastructure.ai.chunked_extraction")

_DISCOVERY_INSTRUCTION = (
    "\n\nFirst, identify the restaurant profile and the menu structure "
    "only: for every menu, list its categories by name. Do NOT list "
    "individual dishes yet — dish-level detail is requested separately, "
    "one category at a time."
)

_CATEGORY_INSTRUCTION_TEMPLATE = (
    "\n\nFrom the same source material, extract every dish that belongs "
    "specifically to the menu category \"{category_name}\" (part of "
    "\"{menu_name}\"). Only report dishes actually listed under this "
    "category — do not include dishes from other categories."
)

# Per-category AI calls for one extraction run happen concurrently
# (bounded) rather than one giant sequential loop — a restaurant with a
# large menu can have a couple dozen categories, and running those fully
# serially would make a single collector run needlessly slow. Kept low
# (rather than e.g. 5): every one of these calls resends the same full
# user_content, so a higher value bursts more of a low-TPM-tier
# account's per-minute budget in the same second — OpenAIProvider's own
# 429 retry/backoff (infrastructure/ai/openai_provider.py) absorbs
# what's left, but a smaller burst means fewer calls need to wait at all.
_MAX_CONCURRENT_CATEGORY_CALLS = 2


async def run_chunked_extraction(
    ai_provider: AIProvider,
    *,
    system_prompt: str,
    user_content: str,
) -> AIProviderResult[ExtractionOutput]:
    """Runs the phase 1 discovery call, then one phase 2 call per
    discovered category, and assembles the results into a single
    `ExtractionOutput`, wrapped the same way a single
    `AIProvider.generate_structured` call's result would be. Raises
    `AIProviderError` if the discovery call fails or if any per-category
    call fails (never returns a partial/best-effort object) — mirrors
    `AIProvider.generate_structured`'s own contract."""

    discovery_result = await ai_provider.generate_structured(
        system_prompt=system_prompt + _DISCOVERY_INSTRUCTION,
        user_content=user_content,
        response_model=ExtractedDiscovery,
    )
    discovery = discovery_result.output

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CATEGORY_CALLS)
    confidences: list[float | None] = [discovery_result.overall_confidence]

    async def _extract_category(stub: ExtractedMenuCategoryStub) -> ExtractedMenuCategory:
        async with semaphore:
            category_result = await ai_provider.generate_structured(
                system_prompt=system_prompt
                + _CATEGORY_INSTRUCTION_TEMPLATE.format(
                    category_name=stub.category_name, menu_name=stub.menu_name
                ),
                user_content=user_content,
                response_model=ExtractedCategoryDishes,
            )
            confidences.append(category_result.overall_confidence)
            return ExtractedMenuCategory(name=stub.category_name, dishes=category_result.output.dishes)

    mapped_categories = (
        await asyncio.gather(*(_extract_category(stub) for stub in discovery.categories))
        if discovery.categories
        else []
    )

    menus: dict[str, list[ExtractedMenuCategory]] = {}
    menu_order: list[str] = []
    for stub, category in zip(discovery.categories, mapped_categories):
        if stub.menu_name not in menus:
            menus[stub.menu_name] = []
            menu_order.append(stub.menu_name)
        menus[stub.menu_name].append(category)

    output = ExtractionOutput(
        restaurant_profile=discovery.restaurant_profile,
        menus=[ExtractedMenu(name=menu_name, categories=menus[menu_name]) for menu_name in menu_order],
    )

    # Only report an overall confidence when every call in the chain
    # reported one — averaging a mix of real signals and Nones would
    # fabricate a number no individual call actually stood behind.
    reported = [c for c in confidences if c is not None]
    overall_confidence = sum(reported) / len(reported) if reported and len(reported) == len(confidences) else None

    return AIProviderResult(
        output=output,
        model_name=discovery_result.model_name,
        overall_confidence=overall_confidence,
    )
