"""Unit tests for verify_and_persist_manual_source — the "skip search,
still validate" helper backing the manual ingestion queue. Reuses
validate_official_domain (same aggregator-blocklist/domain checks a
search-based candidate goes through) and SourceRepository.create (the
same single write path SourceAuthorityService._finalize uses)."""

import uuid

import pytest

from apps.api.app.services.manual_source_verification import (
    ManualSourceRejectedError,
    verify_and_persist_manual_source,
)
from core.schemas.source import SourceType
from database.repositories.source_repository import SourceRepository

pytestmark = pytest.mark.asyncio


class TestVerifyAndPersistManualSource:
    async def test_valid_official_url_persists_a_verified_source(self, db_session) -> None:
        restaurant_id = uuid.uuid4()

        source = await verify_and_persist_manual_source(
            db_session, restaurant_id=restaurant_id, raw_url="https://www.example-restaurant.com/menu"
        )

        assert source.restaurant_id == restaurant_id
        assert source.source_type == SourceType.RESTAURANT_WEBSITE
        assert source.is_verified_domain is True

        persisted = await SourceRepository(db_session).get_by_id(source.id)
        assert persisted is not None
        assert persisted.url == source.url

    async def test_known_aggregator_domain_is_rejected_and_nothing_persisted(self, db_session) -> None:
        restaurant_id = uuid.uuid4()

        with pytest.raises(ManualSourceRejectedError):
            await verify_and_persist_manual_source(
                db_session, restaurant_id=restaurant_id, raw_url="https://www.yelp.com/biz/some-restaurant"
            )

        sources = await SourceRepository(db_session).list_for_restaurant(restaurant_id)
        assert sources == []

    async def test_ip_literal_host_is_rejected(self, db_session) -> None:
        with pytest.raises(ManualSourceRejectedError):
            await verify_and_persist_manual_source(
                db_session, restaurant_id=uuid.uuid4(), raw_url="http://169.254.169.254/menu"
            )
