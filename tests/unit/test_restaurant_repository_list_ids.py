"""Unit tests for RestaurantRepository.list_ids and list_paginated —
list_ids was added for the maintenance-polling worker job (enumerates
every published restaurant to sweep), list_paginated for the admin
dashboard's restaurant list page. Everything else on this repository is
already covered via the collector/reviewer publish node tests.
"""

import uuid
from decimal import Decimal

import pytest

from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.repositories.restaurant_repository import RestaurantRepository

pytestmark = pytest.mark.asyncio


def _restaurant(name: str, *, city: str | None = "Springfield", dish_count: int = 0) -> Restaurant:
    locations = [RestaurantLocation(address_line1="1 Main St", city=city, country="US")] if city else []
    menus = []
    if dish_count:
        dishes = [Dish(category_id=uuid.uuid4(), name=f"Dish {i}", price=Decimal("9.99")) for i in range(dish_count)]
        category = MenuCategory(name="Mains", dishes=dishes)
        menus = [Menu(categories=[category])]
    return Restaurant(name=name, locations=locations, menus=menus)


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


class TestListPaginated:
    async def test_returns_summary_fields(self, db_session) -> None:
        repo = RestaurantRepository(db_session)
        restaurant = await repo.persist_tree(_restaurant("Joe's Pizza", city="Austin", dish_count=3))

        summaries, total = await repo.list_paginated(page=1, page_size=20)

        assert total == 1
        summary = summaries[0]
        assert summary.id == restaurant.id
        assert summary.name == "Joe's Pizza"
        assert summary.city == "Austin"
        assert summary.menu_item_count == 3
        assert summary.is_active is True

    async def test_ordered_by_created_at_desc(self, db_session) -> None:
        # created_at uses server_default=func.now(), which Postgres
        # resolves once per transaction — two inserts in the same test
        # transaction can share an identical timestamp, so this asserts
        # the query is sorted (non-increasing created_at) rather than a
        # specific tie-break order between same-instant rows.
        repo = RestaurantRepository(db_session)
        first = await repo.persist_tree(_restaurant("First"))
        second = await repo.persist_tree(_restaurant("Second"))

        summaries, _ = await repo.list_paginated(page=1, page_size=20)

        assert {s.id for s in summaries} == {first.id, second.id}
        assert all(summaries[i].created_at >= summaries[i + 1].created_at for i in range(len(summaries) - 1))

    async def test_pagination_window(self, db_session) -> None:
        repo = RestaurantRepository(db_session)
        for i in range(3):
            await repo.persist_tree(_restaurant(f"Restaurant {i}"))

        page_one, total = await repo.list_paginated(page=1, page_size=2)
        page_two, _ = await repo.list_paginated(page=2, page_size=2)

        assert total == 3
        assert len(page_one) == 2
        assert len(page_two) == 1

    async def test_city_is_none_when_no_locations(self, db_session) -> None:
        repo = RestaurantRepository(db_session)
        await repo.persist_tree(_restaurant("No City", city=None))

        summaries, _ = await repo.list_paginated(page=1, page_size=20)

        assert summaries[0].city is None

    async def test_empty_when_nothing_published(self, db_session) -> None:
        summaries, total = await RestaurantRepository(db_session).list_paginated(page=1, page_size=20)
        assert summaries == []
        assert total == 0
