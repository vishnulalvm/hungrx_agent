"""Unit tests for workflows/reviewer_workflow/nodes/delta_patch.py's
apply_patch — the actual PATCH-style row-level mutation logic
publish.py calls. Run against a real Postgres transaction (see
tests/conftest.py) since it operates directly on ORM rows.

Covers: restaurant-level scalar patching, dish insert/update/delete
precision (an untouched dish's row is never touched), multiple
FieldDeltas on the same dish collapsing into one row update, and that
an unresolvable/empty delta is a safe no-op.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.schemas.diff import DeltaOp, FieldDelta, JSONDelta
from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.models.restaurant import Dish as DishRow
from database.models.restaurant import Restaurant as RestaurantRow
from database.repositories.restaurant_repository import RestaurantRepository
from workflows.reviewer_workflow.nodes.delta_patch import apply_patch

pytestmark = pytest.mark.asyncio


def _restaurant_with_dishes(*, dish_names: list[str]) -> Restaurant:
    category = MenuCategory(name="Mains")
    dishes = [Dish(category_id=category.id, name=n, price=Decimal("9.99")) for n in dish_names]
    category.dishes = dishes
    menu = Menu(categories=[category])
    return Restaurant(
        name="Joe Diner",
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
        menus=[menu],
    )


async def _seed(db_session, restaurant: Restaurant) -> RestaurantRow:
    await RestaurantRepository(db_session).persist_tree(restaurant)
    await db_session.flush()
    return await db_session.get(RestaurantRow, restaurant.id)


class TestRestaurantLevelPatching:
    async def test_patches_a_touched_scalar_field(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Burger"])
        row = await _seed(db_session, restaurant)

        target = restaurant.model_copy(update={"description": "Now with fries."})
        delta = JSONDelta(
            fields=[FieldDelta(path="description", op=DeltaOp.CHANGED, old_value=None, new_value="Now with fries.")]
        )

        await apply_patch(db_session, restaurant_row=row, target=target, delta=delta)

        assert row.description == "Now with fries."

    async def test_untouched_scalar_field_is_not_patched(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Burger"]).model_copy(
            update={"logo_url": "https://example.com/logo.png"}
        )
        row = await _seed(db_session, restaurant)

        # target reports a different logo_url, but the delta doesn't
        # mention it — apply_patch must not touch it.
        target = restaurant.model_copy(update={"logo_url": "https://example.com/new-logo.png"})
        delta = JSONDelta(fields=[])

        await apply_patch(db_session, restaurant_row=row, target=target, delta=delta)

        assert row.logo_url == "https://example.com/logo.png"


class TestDishPatching:
    async def test_changed_dish_field_updates_only_that_dish(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Burger", "Salad"])
        row = await _seed(db_session, restaurant)
        category = restaurant.menus[0].categories[0]
        burger, salad = category.dishes

        updated_burger = burger.model_copy(update={"price": Decimal("11.99")})
        updated_category = category.model_copy(update={"dishes": [updated_burger, salad]})
        target = restaurant.model_copy(update={"menus": [restaurant.menus[0].model_copy(update={"categories": [updated_category]})]})

        delta = JSONDelta(
            fields=[
                FieldDelta(
                    path="menus[0].categories[0].dishes[0].price",
                    op=DeltaOp.CHANGED,
                    old_value="9.99",
                    new_value="11.99",
                )
            ]
        )

        await apply_patch(db_session, restaurant_row=row, target=target, delta=delta)

        burger_row = await db_session.get(DishRow, burger.id)
        assert burger_row.price == Decimal("11.99")

        salad_row = await db_session.get(DishRow, salad.id)
        assert salad_row.price == Decimal("9.99")  # untouched

    async def test_added_dish_is_inserted_without_touching_siblings(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Burger"])
        row = await _seed(db_session, restaurant)
        category = restaurant.menus[0].categories[0]
        burger = category.dishes[0]

        new_dish = Dish(category_id=category.id, name="Fries", price=Decimal("3.50"))
        updated_category = category.model_copy(update={"dishes": [burger, new_dish]})
        target = restaurant.model_copy(update={"menus": [restaurant.menus[0].model_copy(update={"categories": [updated_category]})]})

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

        await apply_patch(db_session, restaurant_row=row, target=target, delta=delta)

        fries_row = await db_session.get(DishRow, new_dish.id)
        assert fries_row is not None
        assert fries_row.name == "Fries"

        burger_row = await db_session.get(DishRow, burger.id)
        assert burger_row is not None  # untouched, still present

    async def test_removed_dish_is_deleted_without_touching_siblings(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Burger", "Salad"])
        row = await _seed(db_session, restaurant)
        category = restaurant.menus[0].categories[0]
        burger, salad = category.dishes

        updated_category = category.model_copy(update={"dishes": [burger]})
        target = restaurant.model_copy(update={"menus": [restaurant.menus[0].model_copy(update={"categories": [updated_category]})]})

        delta = JSONDelta(
            fields=[
                FieldDelta(
                    path="menus[0].categories[0].dishes[1]",
                    op=DeltaOp.REMOVED,
                    old_value=salad.model_dump(mode="json"),
                    new_value=None,
                )
            ]
        )

        await apply_patch(db_session, restaurant_row=row, target=target, delta=delta)

        assert await db_session.get(DishRow, salad.id) is None
        assert await db_session.get(DishRow, burger.id) is not None

    async def test_multiple_field_deltas_on_the_same_dish_collapse_into_one_update(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Burger"])
        row = await _seed(db_session, restaurant)
        category = restaurant.menus[0].categories[0]
        burger = category.dishes[0]

        updated_burger = burger.model_copy(update={"price": Decimal("11.99"), "description": "Juicy."})
        updated_category = category.model_copy(update={"dishes": [updated_burger]})
        target = restaurant.model_copy(update={"menus": [restaurant.menus[0].model_copy(update={"categories": [updated_category]})]})

        delta = JSONDelta(
            fields=[
                FieldDelta(
                    path="menus[0].categories[0].dishes[0].price",
                    op=DeltaOp.CHANGED,
                    old_value="9.99",
                    new_value="11.99",
                ),
                FieldDelta(
                    path="menus[0].categories[0].dishes[0].description",
                    op=DeltaOp.CHANGED,
                    old_value=None,
                    new_value="Juicy.",
                ),
            ]
        )

        await apply_patch(db_session, restaurant_row=row, target=target, delta=delta)

        burger_row = await db_session.get(DishRow, burger.id)
        assert burger_row.price == Decimal("11.99")
        assert burger_row.description == "Juicy."


class TestEmptyOrUnresolvableDelta:
    async def test_empty_delta_is_a_safe_no_op(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Burger"])
        row = await _seed(db_session, restaurant)

        rows_before = await db_session.execute(select(DishRow))
        ids_before = {r.id for r in rows_before.scalars().all()}

        await apply_patch(db_session, restaurant_row=row, target=restaurant, delta=JSONDelta(fields=[]))

        rows_after = await db_session.execute(select(DishRow))
        ids_after = {r.id for r in rows_after.scalars().all()}
        assert ids_before == ids_after

    async def test_unresolvable_path_is_ignored_not_raised(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(dish_names=["Burger"])
        row = await _seed(db_session, restaurant)

        delta = JSONDelta(
            fields=[FieldDelta(path="some_unrelated_field", op=DeltaOp.CHANGED, old_value="a", new_value="b")]
        )

        # Must not raise, and must not modify anything.
        await apply_patch(db_session, restaurant_row=row, target=restaurant, delta=delta)
        assert row.name == "Joe Diner"
