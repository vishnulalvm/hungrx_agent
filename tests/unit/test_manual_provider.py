"""Unit test for ManualUrlEntityResolutionProvider — the seam that lets
the collector workflow's existing source_authority_node skip search for
manually-ingested restaurants by handing it a provider that already knows
the one right answer."""

import uuid

import pytest

from core.schemas.source_authority import EntityResolutionQuery
from infrastructure.source_authority.manual_provider import ManualUrlEntityResolutionProvider

pytestmark = pytest.mark.asyncio


class TestManualUrlEntityResolutionProvider:
    async def test_resolve_returns_exactly_one_high_confidence_candidate(self) -> None:
        provider = ManualUrlEntityResolutionProvider("https://www.example-restaurant.com")
        query = EntityResolutionQuery(restaurant_id=uuid.uuid4(), name="Example Restaurant")

        candidates = await provider.resolve(query)

        assert len(candidates) == 1
        assert candidates[0].url == "https://www.example-restaurant.com"
        assert candidates[0].provider_confidence == 1.0
        assert candidates[0].provider_name == "manual"
