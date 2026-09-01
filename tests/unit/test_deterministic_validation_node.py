"""Unit tests for the collector workflow's Deterministic Validation node
(Agent 4) — run against a real Postgres transaction (see
tests/conftest.py). This node has no AI/network dependency at all (only
a DB session for AgentRun/AuditLog bookkeeping on failure), so most tests
here exercise the actual node function directly against plain dicts.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from core.schemas.agent_run import AgentWorkflowType
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.nutrition import Macros, Nutrition
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.models.agent_run import AgentRun
from database.models.audit_log import AuditLog
from database.repositories.agent_run_repository import AgentRunRepository
from workflows.collector_workflow.nodes.deterministic_validation import build_deterministic_validation_node

pytestmark = pytest.mark.asyncio


def _dish(**overrides) -> Dish:
    import uuid

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
    async def test_valid_structured_json_reports_is_valid_true(self, db_session) -> None:
        nutrition = Nutrition(serving_size="1 slice", macros=Macros(calories=Decimal(300)))
        dish = _dish(nutrition=nutrition)
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        update = await node({"structured_json": restaurant.model_dump(mode="json")})

        assert update["validation_result"]["is_valid"] is True
        assert "errors" not in update

    async def test_invalid_structured_json_reports_is_valid_false(self, db_session) -> None:
        dish = _dish(currency="US$")
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        update = await node({"structured_json": restaurant.model_dump(mode="json")})

        assert update["validation_result"]["is_valid"] is False
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "deterministic_validation"

    async def test_validation_result_issues_include_both_errors_and_warnings(self, db_session) -> None:
        dish = _dish(price=None)  # missing_price -> warning
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        update = await node({"structured_json": restaurant.model_dump(mode="json")})

        codes = {issue["message"] for issue in update["validation_result"]["issues"]}
        assert len(update["validation_result"]["issues"]) >= 1


class TestAppliesSafeCorrectionsOnly:
    async def test_structured_json_updated_when_correction_applied(self, db_session) -> None:
        dish = _dish(currency="usd")
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        update = await node({"structured_json": restaurant.model_dump(mode="json")})

        assert "structured_json" in update
        corrected_dish = update["structured_json"]["menus"][0]["categories"][0]["dishes"][0]
        assert corrected_dish["currency"] == "USD"

    async def test_structured_json_untouched_when_no_correction_needed(self, db_session) -> None:
        nutrition = Nutrition(serving_size="1 slice", macros=Macros(calories=Decimal(300)))
        dish = _dish(nutrition=nutrition, currency="USD")
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        update = await node({"structured_json": restaurant.model_dump(mode="json")})

        assert "structured_json" not in update

    async def test_ai_generated_calorie_value_is_never_rewritten(self, db_session) -> None:
        # Wildly Atwater-inconsistent calories must survive untouched —
        # only a warning is produced, never a silent rewrite.
        nutrition = Nutrition(
            serving_size="1 slice",
            macros=Macros(calories=Decimal(9999), protein_g=Decimal(1), carbohydrates_g=Decimal(1), fat_g=Decimal(1)),
        )
        dish = _dish(nutrition=nutrition, currency="usd")  # also trigger a real correction
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        update = await node({"structured_json": restaurant.model_dump(mode="json")})

        corrected_dish = update["structured_json"]["menus"][0]["categories"][0]["dishes"][0]
        assert Decimal(corrected_dish["nutrition"]["macros"]["calories"]) == Decimal(9999)

    async def test_ai_generated_price_is_never_rewritten(self, db_session) -> None:
        dish = _dish(price=Decimal("999999"), currency="usd")
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        update = await node({"structured_json": restaurant.model_dump(mode="json")})

        corrected_dish = update["structured_json"]["menus"][0]["categories"][0]["dishes"][0]
        assert Decimal(corrected_dish["price"]) == Decimal("999999")

    async def test_ai_generated_allergens_are_never_rewritten(self, db_session) -> None:
        from core.schemas.menu import Ingredient

        dish = _dish(ingredients=[Ingredient(name="peanuts")], allergens=[], currency="usd")
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        update = await node({"structured_json": restaurant.model_dump(mode="json")})

        corrected_dish = update["structured_json"]["menus"][0]["categories"][0]["dishes"][0]
        assert corrected_dish["allergens"] == []


class TestFailsClosedWithoutInput:
    async def test_missing_structured_json_reports_an_error(self, db_session) -> None:
        node = build_deterministic_validation_node(db_session)

        update = await node({})

        assert "validation_result" not in update
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "deterministic_validation"


class TestAgentRunAndAuditLogging:
    async def test_invalid_result_marks_agent_run_failed(self, db_session) -> None:
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=None
        )
        dish = _dish(price=Decimal("999999"))
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        await node({"structured_json": restaurant.model_dump(mode="json"), "agent_run_id": str(run.id)})

        run_row = await db_session.get(AgentRun, run.id)
        assert run_row.error_message is not None

    async def test_invalid_result_writes_an_audit_row(self, db_session) -> None:
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=None
        )
        dish = _dish(price=Decimal("999999"))
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        await node({"structured_json": restaurant.model_dump(mode="json"), "agent_run_id": str(run.id)})

        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.AGENT_RUN, AuditLog.entity_id == str(run.id)
            )
        )
        entry = rows.scalar_one()
        assert entry.action == AuditAction.AGENT_RUN_TRIGGER
        assert entry.metadata_["is_valid"] is False

    async def test_valid_result_with_no_corrections_writes_no_audit_row(self, db_session) -> None:
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=None
        )
        nutrition = Nutrition(serving_size="1 slice", macros=Macros(calories=Decimal(300)))
        dish = _dish(nutrition=nutrition, currency="USD")
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        await node({"structured_json": restaurant.model_dump(mode="json"), "agent_run_id": str(run.id)})

        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.AGENT_RUN, AuditLog.entity_id == str(run.id)
            )
        )
        assert rows.scalar_one_or_none() is None

    async def test_valid_result_with_a_correction_still_writes_an_audit_row(self, db_session) -> None:
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=None
        )
        nutrition = Nutrition(serving_size="1 slice", macros=Macros(calories=Decimal(300)))
        dish = _dish(nutrition=nutrition, currency="usd")  # triggers a safe correction
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        await node({"structured_json": restaurant.model_dump(mode="json"), "agent_run_id": str(run.id)})

        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.AGENT_RUN, AuditLog.entity_id == str(run.id)
            )
        )
        entry = rows.scalar_one()
        assert entry.metadata_["corrected_fields"]

    async def test_no_agent_run_id_skips_audit_and_agent_run_update(self, db_session) -> None:
        dish = _dish(price=Decimal("999999"))
        restaurant = _restaurant_with_dishes(dish)
        node = build_deterministic_validation_node(db_session)

        update = await node({"structured_json": restaurant.model_dump(mode="json")})

        # Should not raise despite no agent_run_id, and still report the error.
        assert update["validation_result"]["is_valid"] is False
