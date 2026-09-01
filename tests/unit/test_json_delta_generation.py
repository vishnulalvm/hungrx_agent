"""Unit tests for the reviewer workflow's JSON Delta Generation node
(Reviewer Workflow Agent 7, part 2: core.schemas.diff.JSONDelta
production). `compute_delta` itself is pure logic (no DB/network) and
tested directly against plain Restaurant instances; the node wrapper
requires a real Postgres session (see tests/conftest.py) since it now
loads production data via RestaurantRepository.get_full_tree rather than
trusting state["restaurant"] — see json_delta_generation.py's docstring
on "compare with production data."
"""

import uuid
from decimal import Decimal

import pytest

from core.schemas.diff import DeltaOp
from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.repositories.restaurant_repository import RestaurantRepository
from workflows.reviewer_workflow.nodes.json_delta_generation import build_json_delta_generation_node, compute_delta


def _restaurant_with_a_dish(*, price: Decimal = Decimal("9.99")) -> Restaurant:
    category = MenuCategory(name="Mains")
    dish = Dish(category_id=category.id, name="Burger", price=price)
    category.dishes = [dish]
    menu = Menu(categories=[category])
    return Restaurant(
        name="Joe Diner",
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
        menus=[menu],
    )


class TestComputeDelta:
    def test_identical_restaurants_produce_an_empty_delta(self) -> None:
        restaurant = _restaurant_with_a_dish()
        delta = compute_delta(restaurant, restaurant.model_copy(deep=True))

        assert delta.is_empty
        assert delta.fields == []

    def test_a_changed_dish_price_is_reported_as_changed(self) -> None:
        current = _restaurant_with_a_dish(price=Decimal("9.99"))
        dish = current.menus[0].categories[0].dishes[0]
        updated_dish = dish.model_copy(update={"price": Decimal("11.99")})
        updated_category = current.menus[0].categories[0].model_copy(update={"dishes": [updated_dish]})
        updated_menu = current.menus[0].model_copy(update={"categories": [updated_category]})
        reextracted = current.model_copy(update={"menus": [updated_menu]})

        delta = compute_delta(current, reextracted)

        assert not delta.is_empty
        assert any(field.op == DeltaOp.CHANGED for field in delta.fields)

    def test_a_new_dish_is_reported_as_added(self) -> None:
        current = _restaurant_with_a_dish()
        category = current.menus[0].categories[0]
        new_dish = Dish(category_id=category.id, name="Fries", price=Decimal("3.50"))
        updated_category = category.model_copy(update={"dishes": [*category.dishes, new_dish]})
        updated_menu = current.menus[0].model_copy(update={"categories": [updated_category]})
        reextracted = current.model_copy(update={"menus": [updated_menu]})

        delta = compute_delta(current, reextracted)

        added = [field for field in delta.fields if field.op == DeltaOp.ADDED]
        assert len(added) == 1

    def test_a_removed_dish_is_reported_as_removed(self) -> None:
        current = _restaurant_with_a_dish()
        category = current.menus[0].categories[0]
        updated_category = category.model_copy(update={"dishes": []})
        updated_menu = current.menus[0].model_copy(update={"categories": [updated_category]})
        reextracted = current.model_copy(update={"menus": [updated_menu]})

        delta = compute_delta(current, reextracted)

        removed = [field for field in delta.fields if field.op == DeltaOp.REMOVED]
        assert len(removed) == 1

    def test_id_and_timestamp_only_differences_are_ignored(self) -> None:
        current = _restaurant_with_a_dish()
        # A copy with a different top-level id/timestamps but otherwise
        # identical content must not show up as noise — the reviewer
        # workflow always compares against the same restaurant_id, so an
        # id difference here would only ever be a construction artifact,
        # never a real content change worth flagging.
        reextracted = current.model_copy(update={"id": uuid.uuid4()})

        delta = compute_delta(current, reextracted)

        assert delta.is_empty

    def test_unrelated_dishes_do_not_appear_changed_when_one_sibling_changes(self) -> None:
        """Regression coverage for iterable_compare_func: without
        id-based list matching, DeepDiff's ignore_order=True can make an
        unrelated sibling look "changed" just because list positions
        shifted after one item's field changed."""
        dish_a = Dish(category_id=uuid.uuid4(), name="Burger", price=Decimal("9.99"))
        dish_b = Dish(category_id=dish_a.category_id, name="Salad", price=Decimal("7.00"))
        category = MenuCategory(name="Mains", dishes=[dish_a, dish_b])
        menu = Menu(categories=[category])
        current = Restaurant(
            name="Joe Diner",
            locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
            menus=[menu],
        )

        dish_a_changed = dish_a.model_copy(update={"price": Decimal("10.99")})
        updated_category = category.model_copy(update={"dishes": [dish_a_changed, dish_b]})
        updated_menu = menu.model_copy(update={"categories": [updated_category]})
        reextracted = current.model_copy(update={"menus": [updated_menu]})

        delta = compute_delta(current, reextracted)

        # Only dish_a's change should appear — dish_b (unchanged) must
        # not be reported at all.
        touched_dish_names = set()
        for field in delta.fields:
            if isinstance(field.old_value, dict) and "name" in field.old_value:
                touched_dish_names.add(field.old_value["name"])
            if isinstance(field.new_value, dict) and "name" in field.new_value:
                touched_dish_names.add(field.new_value["name"])
        assert "Salad" not in touched_dish_names

    def test_source_refs_are_attached_to_a_changed_dish_field(self) -> None:
        current = _restaurant_with_a_dish(price=Decimal("9.99"))
        dish = current.menus[0].categories[0].dishes[0]
        updated_dish = dish.model_copy(update={"price": Decimal("11.99")})
        updated_category = current.menus[0].categories[0].model_copy(update={"dishes": [updated_dish]})
        updated_menu = current.menus[0].model_copy(update={"categories": [updated_category]})
        reextracted = current.model_copy(update={"menus": [updated_menu]})

        delta = compute_delta(
            current, reextracted, source_refs={str(dish.id): ["snap-1", "snap-2"]}
        )

        changed = [f for f in delta.fields if f.op == DeltaOp.CHANGED]
        assert len(changed) == 1
        assert changed[0].source_snapshot_ids == ["snap-1", "snap-2"]

    def test_source_refs_are_attached_to_restaurant_profile_fields(self) -> None:
        current = _restaurant_with_a_dish()
        reextracted = current.model_copy(update={"description": "A brand new description."})

        delta = compute_delta(
            current, reextracted, source_refs={"restaurant_profile": ["snap-9"]}
        )

        description_field = next(f for f in delta.fields if f.path == "description")
        assert description_field.source_snapshot_ids == ["snap-9"]

    def test_no_source_refs_provided_leaves_field_deltas_with_empty_refs(self) -> None:
        current = _restaurant_with_a_dish(price=Decimal("9.99"))
        dish = current.menus[0].categories[0].dishes[0]
        updated_dish = dish.model_copy(update={"price": Decimal("11.99")})
        updated_category = current.menus[0].categories[0].model_copy(update={"dishes": [updated_dish]})
        updated_menu = current.menus[0].model_copy(update={"categories": [updated_category]})
        reextracted = current.model_copy(update={"menus": [updated_menu]})

        delta = compute_delta(current, reextracted)

        assert all(field.source_snapshot_ids == [] for field in delta.fields)


@pytest.mark.asyncio
class TestJSONDeltaGenerationNode:
    async def test_fails_closed_without_reextracted_json(self, db_session) -> None:
        node = build_json_delta_generation_node(db_session)

        update = await node({"restaurant": _restaurant_with_a_dish()})

        assert "delta" not in update
        assert len(update["errors"]) == 1

    async def test_fails_closed_without_restaurant(self, db_session) -> None:
        node = build_json_delta_generation_node(db_session)
        restaurant = _restaurant_with_a_dish()

        update = await node({"reextracted_structured_json": restaurant.model_dump(mode="json")})

        assert "delta" not in update
        assert len(update["errors"]) == 1

    async def test_fails_closed_when_no_production_data_exists(self, db_session) -> None:
        # restaurant is on state, but nothing was ever persisted to the
        # production tables for its id — "compare with production data"
        # has nothing to compare against.
        node = build_json_delta_generation_node(db_session)
        restaurant = _restaurant_with_a_dish()

        update = await node(
            {
                "restaurant": restaurant,
                "reextracted_structured_json": restaurant.model_dump(mode="json"),
            }
        )

        assert "delta" not in update
        assert len(update["errors"]) == 1

    async def test_produces_a_delta_on_success(self, db_session) -> None:
        current = _restaurant_with_a_dish()
        await RestaurantRepository(db_session).persist_tree(current)
        await db_session.flush()

        node = build_json_delta_generation_node(db_session)
        reextracted = current.model_copy(deep=True)

        update = await node(
            {
                "restaurant": current,
                "reextracted_structured_json": reextracted.model_dump(mode="json"),
            }
        )

        assert "delta" in update
        assert update["delta"].is_empty

    async def test_compares_against_production_data_not_stale_state_restaurant(self, db_session) -> None:
        # state["restaurant"] is deliberately stale (a different name
        # than what's actually published) — the node must diff against
        # the real production row, not this stale copy.
        published = _restaurant_with_a_dish()
        await RestaurantRepository(db_session).persist_tree(published)
        await db_session.flush()

        stale_state_restaurant = published.model_copy(update={"name": "Some Stale Cached Name"})
        node = build_json_delta_generation_node(db_session)

        # Re-extraction agrees with what's actually published (not the
        # stale state copy) — so the delta must be empty.
        reextracted = published.model_copy(deep=True)

        update = await node(
            {
                "restaurant": stale_state_restaurant,
                "reextracted_structured_json": reextracted.model_dump(mode="json"),
            }
        )

        assert update["delta"].is_empty

    async def test_never_writes_to_production_tables(self, db_session) -> None:
        """"Do not directly update production data" — this node only
        reads (get_full_tree has no write counterpart on
        RestaurantRepository); nothing about running it should ever
        change what's actually published, even when the delta reports
        real differences."""
        from database.models.restaurant import Restaurant as RestaurantRow

        published = _restaurant_with_a_dish()
        await RestaurantRepository(db_session).persist_tree(published)
        await db_session.flush()

        reextracted = published.model_copy(update={"name": "A Completely Different Name"})
        node = build_json_delta_generation_node(db_session)

        update = await node(
            {
                "restaurant": published,
                "reextracted_structured_json": reextracted.model_dump(mode="json"),
            }
        )

        assert not update["delta"].is_empty
        row = await db_session.get(RestaurantRow, published.id)
        assert row.name == "Joe Diner"  # untouched — still the originally published name

    async def test_source_refs_from_state_flow_onto_the_delta(self, db_session) -> None:
        current = _restaurant_with_a_dish(price=Decimal("9.99"))
        await RestaurantRepository(db_session).persist_tree(current)
        await db_session.flush()
        dish = current.menus[0].categories[0].dishes[0]

        updated_dish = dish.model_copy(update={"price": Decimal("11.99")})
        updated_category = current.menus[0].categories[0].model_copy(update={"dishes": [updated_dish]})
        updated_menu = current.menus[0].model_copy(update={"categories": [updated_category]})
        reextracted = current.model_copy(update={"menus": [updated_menu]})

        node = build_json_delta_generation_node(db_session)
        update = await node(
            {
                "restaurant": current,
                "reextracted_structured_json": reextracted.model_dump(mode="json"),
                "reextraction_source_refs": {str(dish.id): ["snap-abc"]},
            }
        )

        changed = [f for f in update["delta"].fields if f.op == DeltaOp.CHANGED]
        assert len(changed) == 1
        assert changed[0].source_snapshot_ids == ["snap-abc"]
