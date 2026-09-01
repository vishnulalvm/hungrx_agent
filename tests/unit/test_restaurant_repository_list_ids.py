"""Unit test for RestaurantRepository.list_ids — added for the
maintenance-polling worker job, which needs to enumerate every published
restaurant to sweep. Everything else on this repository is already
covered via the collector/reviewer publish node tests.
"""

import pytest

from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.repositories.restaurant_repository import RestaurantRepository

pytestmark = pytest.mark.asyncio


def _restaurant(name: str) -> Restaurant:
    return Restaurant(
        name=name,
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
    )


class TestListIds:
    async def test_returns_every_published_restaurant_id(self, db_session) -> None:
        repo = RestaurantRepository(db_session)
        first = await repo.persist_tree(_restaurant("Joe's Pizza"))
        second = await repo.persist_tree(_restaurant("Anna's Diner"))

        ids = await repo.list_ids()

        assert first.id in ids
        assert second.id in ids

    async def test_empty_when_nothing_published(self, db_session) -> None:
        ids = await RestaurantRepository(db_session).list_ids()
        assert ids == []
