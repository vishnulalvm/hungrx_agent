"""Unit tests for the reviewer workflow's Publish node —
workflows/reviewer_workflow/nodes/publish.py. Distinct from
tests/unit/test_publish_node.py (the collector workflow's publish node):
this node updates an already-published restaurant rather than inserting
a brand-new one, so it has no "republish guard" (that guard is specific
to the collector workflow inserting a first-ever restaurant) but does
require the restaurant to already exist in production.
"""

import uuid
from decimal import Decimal

import pytest

from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.proposed_change import ProposedChangeEntityType, ProposedChangeStatus
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.models.restaurant import Restaurant as RestaurantRow
from database.repositories.proposed_change_repository import ProposedChangeRepository
from database.repositories.restaurant_repository import RestaurantRepository
from workflows.reviewer_workflow.nodes.publish import build_publish_node

pytestmark = pytest.mark.asyncio


def _restaurant_with_a_dish(*, name: str = "Joe's Pizza") -> Restaurant:
    dish = Dish(category_id=uuid.uuid4(), name="Margherita Pizza", price=Decimal("12.99"))
    category = MenuCategory(name="Pizzas", dishes=[dish])
    menu = Menu(categories=[category])
    return Restaurant(
        name=name,
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
        menus=[menu],
    )


async def _pending_proposed_change(db_session, restaurant: Restaurant):
    return await ProposedChangeRepository(db_session).create(
        entity_type=ProposedChangeEntityType.RESTAURANT,
        entity_id=restaurant.id,
        structured_json=restaurant.model_dump(mode="json"),
        validation_result={"is_valid": True, "issues": []},
        agent_run_id=None,
        source_id=None,
        thread_id=None,
    )


class TestUpdatesAnExistingRestaurant:
    async def test_writes_updated_fields_to_the_existing_row(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        updated = restaurant.model_copy(update={"name": "Joe's Pizza (Updated)"})
        proposed_change = await _pending_proposed_change(db_session, updated)
        node = build_publish_node(db_session)

        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": updated.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
            }
        )

        assert update["published_restaurant_id"] == str(restaurant.id)
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row.name == "Joe's Pizza (Updated)"

    async def test_marks_proposed_change_published(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        proposed_change = await _pending_proposed_change(db_session, restaurant)
        node = build_publish_node(db_session)

        await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
            }
        )

        updated = await ProposedChangeRepository(db_session).get_by_id(proposed_change.id)
        assert updated.status == ProposedChangeStatus.PUBLISHED


class TestRefusesWhenNoExistingRestaurant:
    async def test_refuses_to_publish_a_never_published_entity(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()  # never persisted
        proposed_change = await _pending_proposed_change(db_session, restaurant)
        node = build_publish_node(db_session)

        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
            }
        )

        assert "published_restaurant_id" not in update
        assert len(update["errors"]) == 1
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None


class TestRevalidatesBeforeCommit:
    async def test_refuses_to_publish_data_that_fails_revalidation(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        proposed_change = await _pending_proposed_change(db_session, restaurant)
        node = build_publish_node(db_session)

        bad_json = restaurant.model_dump(mode="json")
        bad_json["menus"][0]["categories"][0]["dishes"][0]["price"] = "999.00"

        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": bad_json,
                "proposed_change_id": str(proposed_change.id),
            }
        )

        assert "published_restaurant_id" not in update
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row.name == "Joe's Pizza"  # untouched — original still stands


class TestRefusesUnapprovedData:
    async def test_missing_approval_status_does_not_write(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        node = build_publish_node(db_session)
        update = await node({"validated_structured_json": restaurant.model_dump(mode="json")})

        assert "published_restaurant_id" not in update
        assert len(update["errors"]) == 1

    async def test_rejected_status_does_not_write(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        node = build_publish_node(db_session)
        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.REJECTED,
                "validated_structured_json": restaurant.model_dump(mode="json"),
            }
        )

        assert "published_restaurant_id" not in update
