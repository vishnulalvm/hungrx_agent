"""Unit tests for the reviewer workflow's Delta Validation node — runs
against a real Postgres transaction (see tests/conftest.py) for
AgentRun/AuditLog bookkeeping; the validation logic itself is the same
deterministic core.validation.validate engine already covered
exhaustively in tests/unit/test_validation_engine.py, so these tests
focus on the node's own wiring: pass-through/correction behavior,
fail-closed handling, and AgentRun/AuditLog side effects.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.schemas.agent_run import AgentWorkflowType
from core.schemas.diff import DeltaOp, FieldDelta, JSONDelta
from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.nutrition import Macros, Nutrition
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.models.agent_run import AgentRun
from database.repositories.agent_run_repository import AgentRunRepository
from workflows.reviewer_workflow.nodes.delta_validation import build_delta_validation_node

pytestmark = pytest.mark.asyncio


def _dish(**overrides) -> Dish:
    payload = {"category_id": uuid.uuid4(), "name": "Margherita Pizza", "price": Decimal("12.99")}
    payload.update(overrides)
    return Dish(**payload)


def _restaurant_with_dishes(*dishes) -> Restaurant:
    category = MenuCategory(name="Pizzas", dishes=list(dishes))
    menu = Menu(categories=[category])
    return Restaurant(
        name="Joe's Pizza",
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
        menus=[menu],
    )


class TestReturnsValidationResult:
    async def test_valid_reextraction_reports_is_valid_true(self, db_session) -> None:
        nutrition = Nutrition(serving_size="1 slice", macros=Macros(calories=Decimal(300)))
        restaurant = _restaurant_with_dishes(_dish(nutrition=nutrition))
        node = build_delta_validation_node(db_session)

        update = await node({"reextracted_structured_json": restaurant.model_dump(mode="json")})

        assert update["validation_result"]["is_valid"] is True
        assert update["validated_structured_json"] == restaurant.model_dump(mode="json")

    async def test_invalid_reextraction_reports_is_valid_false(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(_dish(currency="US$"))
        node = build_delta_validation_node(db_session)

        update = await node({"reextracted_structured_json": restaurant.model_dump(mode="json")})

        assert update["validation_result"]["is_valid"] is False
        assert any(issue["severity"] == "error" for issue in update["validation_result"]["issues"])


class TestAppliesSafeCorrectionsOnly:
    async def test_validated_json_reflects_a_safe_correction(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(_dish(currency="usd"))
        node = build_delta_validation_node(db_session)

        update = await node({"reextracted_structured_json": restaurant.model_dump(mode="json")})

        corrected_dish = update["validated_structured_json"]["menus"][0]["categories"][0]["dishes"][0]
        assert corrected_dish["currency"] == "USD"

    async def test_ai_generated_price_is_never_rewritten(self, db_session) -> None:
        restaurant = _restaurant_with_dishes(_dish(price=Decimal("999999"), currency="usd"))
        node = build_delta_validation_node(db_session)

        update = await node({"reextracted_structured_json": restaurant.model_dump(mode="json")})

        corrected_dish = update["validated_structured_json"]["menus"][0]["categories"][0]["dishes"][0]
        assert Decimal(corrected_dish["price"]) == Decimal("999999")


class TestScopesReportedIssuesToChangedOrNewData:
    """"Validate only changed/new data where safe": is_valid always
    reflects the FULL, unscoped validation run — only which issues are
    REPORTED gets scoped down to what the delta says is
    ADDED/CHANGED. An issue on a dish nobody touched this run
    (pre-existing, unrelated to whatever changed at the source) is
    filtered out of the report; restaurant-level/schema-level issues are
    never filtered, since they're not attributable to one specific item.
    """

    async def test_issue_on_an_untouched_dish_is_not_reported(self, db_session) -> None:
        # dish 0 has a pre-existing issue (implausible price) but the
        # delta only reports dish 1 as changed — dish 0's issue must not
        # appear in the reported issues, even though is_valid still
        # reflects it.
        untouched_dish_with_issue = _dish(name="Untouched Burger", price=Decimal("999"))
        touched_dish = _dish(name="Changed Fries", price=Decimal("5.00"))
        restaurant = _restaurant_with_dishes(untouched_dish_with_issue, touched_dish)
        node = build_delta_validation_node(db_session)

        delta = JSONDelta(
            fields=[
                FieldDelta(
                    path="menus[0].categories[0].dishes[1].price",
                    op=DeltaOp.CHANGED,
                    old_value="4.00",
                    new_value="5.00",
                )
            ]
        )

        update = await node(
            {"reextracted_structured_json": restaurant.model_dump(mode="json"), "delta": delta}
        )

        # Full validation still finds the untouched dish's issue (so
        # is_valid still reflects reality)...
        assert update["validation_result"]["is_valid"] is False
        # ...but it's not among the REPORTED issues, since that dish
        # wasn't touched by this run's delta.
        reported_paths = [issue["field_path"] for issue in update["validation_result"]["issues"]]
        assert not any("dishes[0]" in path for path in reported_paths)

    async def test_issue_on_a_touched_dish_is_reported(self, db_session) -> None:
        touched_dish_with_issue = _dish(name="Changed Burger", price=Decimal("999"))
        restaurant = _restaurant_with_dishes(touched_dish_with_issue)
        node = build_delta_validation_node(db_session)

        delta = JSONDelta(
            fields=[
                FieldDelta(
                    path="menus[0].categories[0].dishes[0].price",
                    op=DeltaOp.CHANGED,
                    old_value="4.00",
                    new_value="999",
                )
            ]
        )

        update = await node(
            {"reextracted_structured_json": restaurant.model_dump(mode="json"), "delta": delta}
        )

        reported_paths = [issue["field_path"] for issue in update["validation_result"]["issues"]]
        assert any("dishes[0]" in path for path in reported_paths)

    async def test_no_delta_on_state_reports_every_issue_unscoped(self, db_session) -> None:
        # Backwards-compatible behavior: without a delta at all (e.g. a
        # direct call, or an earlier node failed to produce one), every
        # issue is reported — same as before scoping existed.
        restaurant = _restaurant_with_dishes(_dish(price=Decimal("999")))
        node = build_delta_validation_node(db_session)

        update = await node({"reextracted_structured_json": restaurant.model_dump(mode="json")})

        reported_paths = [issue["field_path"] for issue in update["validation_result"]["issues"]]
        assert any("dishes[0]" in path for path in reported_paths)

    async def test_restaurant_level_issue_is_never_filtered(self, db_session) -> None:
        # A restaurant with no locations triggers a required-field
        # error at the restaurant level, not scoped to any dish — must
        # always be reported regardless of what the delta touched.
        dish = _dish()
        category = MenuCategory(name="Pizzas", dishes=[dish])
        menu = Menu(categories=[category])
        restaurant = Restaurant(name="Joe's Pizza", locations=[], menus=[menu])
        node = build_delta_validation_node(db_session)

        # Delta only reports a dish-level change — nothing restaurant-level.
        delta = JSONDelta(
            fields=[
                FieldDelta(
                    path="menus[0].categories[0].dishes[0].price",
                    op=DeltaOp.CHANGED,
                    old_value="10.00",
                    new_value="12.99",
                )
            ]
        )

        update = await node(
            {"reextracted_structured_json": restaurant.model_dump(mode="json"), "delta": delta}
        )

        reported_paths = [issue["field_path"] for issue in update["validation_result"]["issues"]]
        assert "locations" in reported_paths


class TestFailsClosedWithoutInput:
    async def test_missing_reextracted_json_reports_an_error(self, db_session) -> None:
        node = build_delta_validation_node(db_session)

        update = await node({})

        assert "validation_result" not in update
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "delta_validation"


class TestAgentRunBookkeeping:
    async def test_invalid_result_marks_agent_run_failed(self, db_session) -> None:
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.REVIEWER, restaurant_id=None
        )
        restaurant = _restaurant_with_dishes(_dish(price=Decimal("999999")))
        node = build_delta_validation_node(db_session)

        await node({"reextracted_structured_json": restaurant.model_dump(mode="json"), "agent_run_id": str(run.id)})

        run_row = await db_session.get(AgentRun, run.id)
        assert run_row.error_message is not None

    async def test_valid_result_with_no_corrections_leaves_agent_run_untouched(self, db_session) -> None:
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.REVIEWER, restaurant_id=None
        )
        nutrition = Nutrition(serving_size="1 slice", macros=Macros(calories=Decimal(300)))
        restaurant = _restaurant_with_dishes(_dish(nutrition=nutrition, currency="USD"))
        node = build_delta_validation_node(db_session)

        await node({"reextracted_structured_json": restaurant.model_dump(mode="json"), "agent_run_id": str(run.id)})

        run_row = await db_session.get(AgentRun, run.id)
        assert run_row.error_message is None
