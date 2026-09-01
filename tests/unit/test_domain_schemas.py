"""Unit tests for the core Pydantic domain schemas — pure validation
logic, no DB/HTTP involved."""

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.schemas.agent_run import AgentRun, AgentWorkflowType
from core.schemas.diff import DeltaOp, FieldDelta, JSONDelta
from core.schemas.menu import Allergen, Dish, Menu, MenuCategory, ReviewState
from core.schemas.nutrition import Macros, Nutrition
from core.schemas.proposed_change import ProposedChange, ProposedChangeEntityType
from core.schemas.restaurant import Restaurant, RestaurantLocation
from core.schemas.source import Source, SourceSnapshot, SourceType, SnapshotContentType


def _minimal_dish(**overrides) -> Dish:
    payload = {"category_id": uuid.uuid4(), "name": "Cheeseburger"}
    payload.update(overrides)
    return Dish(**payload)


class TestNutritionRejectsArbitraryKeys:
    def test_macros_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            Macros(calories=500, some_ai_invented_field=123)

    def test_nutrition_rejects_unknown_top_level_field(self) -> None:
        with pytest.raises(ValidationError):
            Nutrition(made_up_key="oops")

    def test_micronutrients_rejects_unknown_field_nested_in_dish(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_dish(
                nutrition={
                    "macros": {"calories": 100},
                    "micronutrients": {"unknown_vitamin_xyz": 5},
                }
            )

    def test_known_macro_fields_are_accepted(self) -> None:
        macros = Macros(calories=650, protein_g=Decimal("35.5"), sodium_mg=900)
        assert macros.calories == 650
        assert macros.protein_g == Decimal("35.5")


class TestMenuCategoryRecursiveNesting:
    def test_arbitrary_depth_nesting(self) -> None:
        leaf = MenuCategory(name="Iced Coffees")
        mid = MenuCategory(name="Coffee", children=[leaf])
        root = MenuCategory(name="Drinks", children=[mid])

        assert root.children[0].children[0].name == "Iced Coffees"

    def test_category_can_hold_both_children_and_dishes(self) -> None:
        dish = _minimal_dish()
        category = MenuCategory(name="Burgers", dishes=[dish])
        sub = MenuCategory(name="Specials", children=[category])

        assert sub.children[0].dishes[0].name == "Cheeseburger"

    def test_blank_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MenuCategory(name="   ")

    def test_empty_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MenuCategory(name="")

    def test_name_is_stripped(self) -> None:
        category = MenuCategory(name="  Appetizers  ")
        assert category.name == "Appetizers"

    def test_category_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            MenuCategory(name="Drinks", some_extra_field="nope")


class TestDishFixedFields:
    def test_minimal_dish_has_defaults(self) -> None:
        dish = _minimal_dish()
        assert dish.review_state == ReviewState.PENDING
        assert dish.allergens == []
        assert dish.currency == "USD"

    def test_full_dish_round_trips(self) -> None:
        dish = _minimal_dish(
            description="A juicy burger",
            image_url="https://example.com/burger.jpg",
            nutrition={
                "serving_size": "1 burger (280g)",
                "macros": {"calories": 650, "protein_g": 35},
            },
            allergens=["milk", "wheat", "milk"],
            quantity="1 piece",
            price="12.99",
            review_state="confirmed",
        )
        assert dish.nutrition.macros.calories == 650
        # duplicate allergen collapsed
        assert dish.allergens == [Allergen.MILK, Allergen.WHEAT]
        assert dish.price == Decimal("12.99")
        assert dish.review_state == ReviewState.CONFIRMED

    def test_blank_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_dish(name="   ")

    def test_negative_price_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_dish(price="-5.00")

    def test_dish_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_dish(some_ai_field="hallucinated")

    def test_invalid_allergen_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_dish(allergens=["durian"])


class TestMenu:
    def test_menu_holds_category_tree(self) -> None:
        category = MenuCategory(name="Mains", dishes=[_minimal_dish()])
        menu = Menu(categories=[category])
        assert menu.categories[0].dishes[0].name == "Cheeseburger"

    def test_menu_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            Menu(extra_thing=1)


class TestRestaurant:
    def test_restaurant_supports_images_locations_and_menus(self) -> None:
        location = RestaurantLocation(
            address_line1="123 Main St", city="Springfield", country="us"
        )
        menu = Menu(categories=[MenuCategory(name="Mains")])
        restaurant = Restaurant(
            name="Test Bistro",
            logo_url="https://example.com/logo.png",
            cover_image_url="https://example.com/cover.png",
            locations=[location],
            menus=[menu],
        )
        assert restaurant.locations[0].country == "US"
        assert restaurant.menus[0].categories[0].name == "Mains"

    def test_blank_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Restaurant(name="")

    def test_restaurant_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            Restaurant(name="Test", not_a_real_field=True)

    def test_location_blank_address_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RestaurantLocation(address_line1="  ", city="Springfield", country="US")

    def test_location_rejects_bad_country_code_length(self) -> None:
        with pytest.raises(ValidationError):
            RestaurantLocation(address_line1="123 Main St", city="X", country="USA")


class TestSourceAndSnapshot:
    def test_source_requires_url(self) -> None:
        with pytest.raises(ValidationError):
            Source(restaurant_id=uuid.uuid4(), source_type=SourceType.RESTAURANT_WEBSITE, url="")

    def test_snapshot_requires_full_sha256_hex_length(self) -> None:
        with pytest.raises(ValidationError):
            SourceSnapshot(
                source_id=uuid.uuid4(),
                content_type=SnapshotContentType.HTML,
                content_hash="deadbeef",  # too short for SHA-256 hex
                storage_path="s3://bucket/key",
                fetched_at="2026-01-01T00:00:00Z",
            )

    def test_valid_snapshot(self) -> None:
        snapshot = SourceSnapshot(
            source_id=uuid.uuid4(),
            content_type=SnapshotContentType.PDF,
            content_hash="a" * 64,
            storage_path="s3://bucket/key",
            fetched_at="2026-01-01T00:00:00Z",
        )
        assert snapshot.content_type == SnapshotContentType.PDF


class TestJSONDelta:
    def test_delta_holds_field_changes(self) -> None:
        delta = JSONDelta(
            fields=[
                FieldDelta(path="name", op=DeltaOp.CHANGED, old_value="Old", new_value="New"),
                FieldDelta(path="logo_url", op=DeltaOp.ADDED, new_value="https://x/y.png"),
            ]
        )
        assert not delta.is_empty
        assert delta.fields[0].op == DeltaOp.CHANGED

    def test_empty_delta(self) -> None:
        assert JSONDelta().is_empty


class TestProposedChangeAndAgentRun:
    def test_proposed_change_can_reference_agent_run(self) -> None:
        run = AgentRun(workflow_type=AgentWorkflowType.COLLECTOR)
        change = ProposedChange(
            entity_type=ProposedChangeEntityType.DISH,
            entity_id=uuid.uuid4(),
            delta=JSONDelta(fields=[FieldDelta(path="price", op=DeltaOp.CHANGED, old_value=10, new_value=12)]),
            agent_run_id=run.id,
        )
        assert change.agent_run_id == run.id

    def test_proposed_change_without_agent_run_is_human_authored(self) -> None:
        change = ProposedChange(
            entity_type=ProposedChangeEntityType.RESTAURANT,
            entity_id=uuid.uuid4(),
            delta=JSONDelta(),
            created_by_user_id=uuid.uuid4(),
        )
        assert change.agent_run_id is None
