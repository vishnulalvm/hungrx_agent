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
