"""Integration tests for SourceAuthorityService against a real Postgres
transaction (see tests/conftest.py). Uses a fake in-memory
EntityResolutionProvider — the module's whole point is that the real
provider stays swappable behind the EntityResolutionProvider interface,
so exercising the service through a fake proves that boundary works."""

import uuid

import pytest
from sqlalchemy import select

from apps.api.app.services.source_authority_service import SourceAuthorityService
from core.schemas.source import SourceType
from core.schemas.source_authority import (
    ConfidenceLevel,
    EntityCandidate,
    EntityResolutionQuery,
    ResolutionStatus,
)
from database.models.source import Source
from infrastructure.source_authority.null_provider import NullEntityResolutionProvider
from infrastructure.source_authority.provider import EntityResolutionProvider

pytestmark = pytest.mark.asyncio


class FakeProvider(EntityResolutionProvider):
    def __init__(self, candidates: list[EntityCandidate]) -> None:
        self._candidates = candidates

    async def resolve(self, query: EntityResolutionQuery) -> list[EntityCandidate]:
        return self._candidates


def _query(**overrides) -> EntityResolutionQuery:
    payload = {"restaurant_id": uuid.uuid4(), "name": "Joe's Pizza", "city": "Springfield"}
    payload.update(overrides)
    return EntityResolutionQuery(**payload)


class TestResolveOfficialWebsiteNotFound:
    async def test_no_candidates_returns_not_found(self, db_session) -> None:
        service = SourceAuthorityService(db_session, NullEntityResolutionProvider())
        result = await service.resolve_official_website(_query())

        assert result.status == ResolutionStatus.NOT_FOUND
        assert result.resolved_url is None
        assert result.source_id is None


class TestResolveOfficialWebsiteVerified:
    async def test_high_confidence_candidate_is_verified_and_persisted(self, db_session) -> None:
        query = _query()
        provider = FakeProvider(
            [EntityCandidate(url="https://joes-pizza.com/", provider_confidence=0.95, provider_name="fake")]
        )
        service = SourceAuthorityService(db_session, provider)

        result = await service.resolve_official_website(query)

        assert result.status == ResolutionStatus.VERIFIED
        assert result.confidence == ConfidenceLevel.HIGH
        assert result.resolved_url == "https://joes-pizza.com/"
        assert result.source_id is not None

        row = await db_session.execute(select(Source).where(Source.id == result.source_id))
        source = row.scalar_one()
        assert source.restaurant_id == query.restaurant_id
        assert source.source_type == SourceType.RESTAURANT_WEBSITE
        assert source.is_verified_domain is True
        assert source.url == "https://joes-pizza.com/"

    async def test_url_is_normalized_before_persistence(self, db_session) -> None:
        # normalize_url lowercases/trims but deliberately keeps a "www."
        # host as-is (only domain *comparison*, via DomainVerifier, treats
        # www and bare host as equivalent) — so the persisted URL reflects
        # what the provider actually returned, just canonicalized.
        provider = FakeProvider(
            [EntityCandidate(url="WWW.JOES-PIZZA.com/menu/", provider_confidence=0.9, provider_name="fake")]
        )
        service = SourceAuthorityService(db_session, provider)

        result = await service.resolve_official_website(_query())

        assert result.resolved_url == "https://www.joes-pizza.com/menu"


class TestResolveOfficialWebsiteNeedsReview:
    async def test_low_confidence_candidate_is_not_auto_persisted(self, db_session) -> None:
        provider = FakeProvider(
            [EntityCandidate(url="https://joes-pizza.com/", provider_confidence=0.4, provider_name="fake")]
        )
        service = SourceAuthorityService(db_session, provider)

        result = await service.resolve_official_website(_query())

        assert result.status == ResolutionStatus.NEEDS_REVIEW
        assert result.confidence == ConfidenceLevel.LOW
        assert result.resolved_url == "https://joes-pizza.com/"
        assert result.source_id is None

        rows = await db_session.execute(select(Source))
        assert rows.scalar_one_or_none() is None

    async def test_medium_confidence_candidate_needs_review(self, db_session) -> None:
        provider = FakeProvider(
            [EntityCandidate(url="https://joes-pizza.com/", provider_confidence=0.6, provider_name="fake")]
        )
        service = SourceAuthorityService(db_session, provider)

        result = await service.resolve_official_website(_query())

        assert result.status == ResolutionStatus.NEEDS_REVIEW
        assert result.confidence == ConfidenceLevel.MEDIUM


class TestResolveOfficialWebsiteRejected:
    async def test_only_aggregator_candidate_is_rejected(self, db_session) -> None:
        provider = FakeProvider(
            [EntityCandidate(url="https://www.yelp.com/biz/joes-pizza", provider_confidence=0.99, provider_name="fake")]
        )
        service = SourceAuthorityService(db_session, provider)

        result = await service.resolve_official_website(_query())

        assert result.status == ResolutionStatus.REJECTED
        assert result.rejected_candidates == ["https://www.yelp.com/biz/joes-pizza"]
        assert result.source_id is None

    async def test_falls_through_to_next_candidate_after_aggregator_rejection(self, db_session) -> None:
        provider = FakeProvider(
            [
                EntityCandidate(url="https://www.yelp.com/biz/joes-pizza", provider_confidence=0.99, provider_name="fake"),
                EntityCandidate(url="https://joes-pizza.com/", provider_confidence=0.85, provider_name="fake"),
            ]
        )
        service = SourceAuthorityService(db_session, provider)

        result = await service.resolve_official_website(_query())

        assert result.status == ResolutionStatus.VERIFIED
        assert result.resolved_url == "https://joes-pizza.com/"
        assert result.rejected_candidates == ["https://www.yelp.com/biz/joes-pizza"]
