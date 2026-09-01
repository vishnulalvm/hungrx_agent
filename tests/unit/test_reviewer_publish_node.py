"""Unit tests for the reviewer workflow's Publish node —
workflows/reviewer_workflow/nodes/publish.py. Distinct from
tests/unit/test_publish_node.py (the collector workflow's publish node):
this node updates an already-published restaurant PATCH-style (only the
rows state["delta"] actually names are touched — see
nodes/delta_patch.py), rather than inserting a brand-new one, so it has
no "republish guard" (that guard is specific to the collector workflow
inserting a first-ever restaurant) but does require the restaurant to
already exist in production.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.schemas.diff import DeltaOp, FieldDelta, JSONDelta
from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.proposed_change import ProposedChangeEntityType, ProposedChangeStatus
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.models.restaurant import Dish as DishRow
from database.models.restaurant import Restaurant as RestaurantRow
from database.repositories.proposed_change_repository import ProposedChangeRepository
from database.repositories.restaurant_repository import RestaurantRepository
from workflows.reviewer_workflow.nodes.publish import build_publish_node

pytestmark = pytest.mark.asyncio


def _restaurant_with_dishes(*, name: str = "Joe's Pizza", dish_names: list[str] = ("Margherita",)) -> Restaurant:
    category = MenuCategory(name="Pizzas")
    dishes = [Dish(category_id=category.id, name=n, price=Decimal("12.99")) for n in dish_names]
    category.dishes = dishes
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


def _empty_delta() -> JSONDelta:
    return JSONDelta(fields=[])


class TestUpdatesAnExistingRestaurantRestaurantLevel:
    async def test_writes_updated_restaurant_level_fields(self, db_session) -> None:
        restaurant = _restaurant_with_dishes()
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        updated = restaurant.model_copy(update={"description": "A new description."})
        proposed_change = await _pending_proposed_change(db_session, updated)
        node = build_publish_node(db_session)

        delta = JSONDelta(
            fields=[FieldDelta(path="description", op=DeltaOp.CHANGED, old_value=None, new_value="A new description.")]
        )

        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": updated.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
                "delta": delta,
            }
        )

        assert update["published_restaurant_id"] == str(restaurant.id)
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row.description == "A new description."

    async def test_marks_proposed_change_published(self, db_session) -> None:
        restaurant = _restaurant_with_dishes()
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        proposed_change = await _pending_proposed_change(db_session, restaurant)
        node = build_publish_node(db_session)

        await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
                "delta": _empty_delta(),
            }
        )

        updated = await ProposedChangeRepository(db_session).get_by_id(proposed_change.id)
        assert updated.status == ProposedChangeStatus.PUBLISHED


class TestPatchStylePrecision:
    """The headline guarantee this task's rewrite is about: only the
    rows the approved delta names are touched. Proven by asserting an
    untouched dish's row keeps its original physical identity/values
    even though the whole restaurant tree was re-validated and could,
    in principle, have been fully replaced instead."""

    async def test_untouched_dish_is_never_modified(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Margherita", "Pepperoni"])
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        category = restaurant.menus[0].categories[0]
        margherita, pepperoni = category.dishes

        updated_margherita = margherita.model_copy(update={"price": Decimal("15.99")})
        updated_category = category.model_copy(update={"dishes": [updated_margherita, pepperoni]})
        updated_menu = restaurant.menus[0].model_copy(update={"categories": [updated_category]})
        updated_restaurant = restaurant.model_copy(update={"menus": [updated_menu]})

        proposed_change = await _pending_proposed_change(db_session, updated_restaurant)
        node = build_publish_node(db_session)

        delta = JSONDelta(
            fields=[
                FieldDelta(
                    path=f"menus[0].categories[0].dishes[0].price",
                    op=DeltaOp.CHANGED,
                    old_value="12.99",
                    new_value="15.99",
                )
            ]
        )

        await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": updated_restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
                "delta": delta,
            }
        )

        margherita_row = await db_session.get(DishRow, margherita.id)
        assert margherita_row.price == Decimal("15.99")

        pepperoni_row = await db_session.get(DishRow, pepperoni.id)
        assert pepperoni_row.price == Decimal("12.99")  # untouched
        assert pepperoni_row.name == "Pepperoni"

    async def test_added_dish_is_inserted(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Margherita"])
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        category = restaurant.menus[0].categories[0]
        new_dish = Dish(category_id=category.id, name="Fries", price=Decimal("3.50"))
        updated_category = category.model_copy(update={"dishes": [*category.dishes, new_dish]})
        updated_menu = restaurant.menus[0].model_copy(update={"categories": [updated_category]})
        updated_restaurant = restaurant.model_copy(update={"menus": [updated_menu]})

        proposed_change = await _pending_proposed_change(db_session, updated_restaurant)
        node = build_publish_node(db_session)

        delta = JSONDelta(
            fields=[
                FieldDelta(
                    path="menus[0].categories[0].dishes[1]",
                    op=DeltaOp.ADDED,
                    old_value=None,
                    new_value=new_dish.model_dump(mode="json"),
                )
            ]
        )

        await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": updated_restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
                "delta": delta,
            }
        )

        new_row = await db_session.get(DishRow, new_dish.id)
        assert new_row is not None
        assert new_row.name == "Fries"

        original_dish_row = await db_session.get(DishRow, category.dishes[0].id)
        assert original_dish_row is not None  # original untouched, still present

    async def test_removed_dish_is_deleted(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Margherita", "Pepperoni"])
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        category = restaurant.menus[0].categories[0]
        margherita, pepperoni = category.dishes

        updated_category = category.model_copy(update={"dishes": [margherita]})
        updated_menu = restaurant.menus[0].model_copy(update={"categories": [updated_category]})
        updated_restaurant = restaurant.model_copy(update={"menus": [updated_menu]})

        proposed_change = await _pending_proposed_change(db_session, updated_restaurant)
        node = build_publish_node(db_session)

        delta = JSONDelta(
            fields=[
                FieldDelta(
                    path="menus[0].categories[0].dishes[1]",
                    op=DeltaOp.REMOVED,
                    old_value=pepperoni.model_dump(mode="json"),
                    new_value=None,
                )
            ]
        )

        await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": updated_restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
                "delta": delta,
            }
        )

        assert await db_session.get(DishRow, pepperoni.id) is None
        margherita_row = await db_session.get(DishRow, margherita.id)
        assert margherita_row is not None  # untouched sibling still present

    async def test_empty_delta_writes_nothing(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Margherita", "Pepperoni"])
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        proposed_change = await _pending_proposed_change(db_session, restaurant)
        node = build_publish_node(db_session)

        rows_before = await db_session.execute(select(DishRow))
        dish_ids_before = {row.id for row in rows_before.scalars().all()}

        await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
                "delta": _empty_delta(),
            }
        )

        rows_after = await db_session.execute(select(DishRow))
        dish_ids_after = {row.id for row in rows_after.scalars().all()}
        assert dish_ids_before == dish_ids_after


class TestRefusesWhenNoExistingRestaurant:
    async def test_refuses_to_publish_a_never_published_entity(self, db_session) -> None:
        restaurant = _restaurant_with_dishes()  # never persisted
        proposed_change = await _pending_proposed_change(db_session, restaurant)
        node = build_publish_node(db_session)

        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "validated_structured_json": restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
                "delta": _empty_delta(),
            }
        )

        assert "published_restaurant_id" not in update
        assert len(update["errors"]) == 1
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None


class TestRevalidatesBeforeCommit:
    async def test_refuses_to_publish_data_that_fails_revalidation(self, db_session) -> None:
        restaurant = _restaurant_with_dishes()
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
                "delta": _empty_delta(),
            }
        )

        assert "published_restaurant_id" not in update
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row.name == "Joe's Pizza"  # untouched — original still stands


class TestRefusesUnapprovedData:
    async def test_missing_approval_status_does_not_write(self, db_session) -> None:
        restaurant = _restaurant_with_dishes()
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        node = build_publish_node(db_session)
        update = await node({"validated_structured_json": restaurant.model_dump(mode="json")})

        assert "published_restaurant_id" not in update
        assert len(update["errors"]) == 1

    async def test_rejected_status_does_not_write(self, db_session) -> None:
        restaurant = _restaurant_with_dishes()
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

        node = build_publish_node(db_session)
        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.REJECTED,
                "validated_structured_json": restaurant.model_dump(mode="json"),
                "delta": _empty_delta(),
            }
        )

        assert "published_restaurant_id" not in update

    async def test_missing_delta_reports_an_error(self, db_session) -> None:
        restaurant = _restaurant_with_dishes()
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.flush()

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
