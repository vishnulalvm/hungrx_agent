"""Unit tests for the collector workflow's Publish node (Agent 6) — the
only place in the codebase allowed to write to the production
restaurant/menu/dish tables (database/models/restaurant.py). Run against
a real Postgres transaction (tests/conftest.py's db_session).

Covers: a real production write on APPROVED, defense-in-depth refusal
when reached without APPROVED (even directly, bypassing graph routing),
ProposedChange/AgentRun/AuditLog bookkeeping, and — the core guarantee
this whole task is built around — that nothing here ever writes
restaurant data without an APPROVED status already on state.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.schemas.agent_run import AgentWorkflowType
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.proposed_change import ProposedChangeEntityType, ProposedChangeStatus
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.models.agent_run import AgentRun
from database.models.audit_log import AuditLog
from database.models.restaurant import Dish as DishRow
from database.models.restaurant import Restaurant as RestaurantRow
from database.repositories.agent_run_repository import AgentRunRepository
from database.repositories.proposed_change_repository import ProposedChangeRepository
from workflows.collector_workflow.nodes.publish import build_publish_node

pytestmark = pytest.mark.asyncio


def _restaurant_with_a_dish() -> Restaurant:
    dish = Dish(category_id=uuid.uuid4(), name="Margherita Pizza", price=Decimal("12.99"))
    category = MenuCategory(name="Pizzas", dishes=[dish])
    menu = Menu(categories=[category])
    return Restaurant(
        name="Joe's Pizza",
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
        menus=[menu],
    )


async def _pending_proposed_change(db_session, restaurant: Restaurant, *, agent_run_id: uuid.UUID | None = None):
    return await ProposedChangeRepository(db_session).create(
        entity_type=ProposedChangeEntityType.RESTAURANT,
        entity_id=restaurant.id,
        structured_json=restaurant.model_dump(mode="json"),
        validation_result={"is_valid": True, "issues": []},
        agent_run_id=agent_run_id,
        source_id=None,
        thread_id=str(agent_run_id) if agent_run_id else None,
    )


class TestPublishesOnApproval:
    async def test_writes_restaurant_row(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        proposed_change = await _pending_proposed_change(db_session, restaurant)
        node = build_publish_node(db_session)

        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "structured_json": restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
            }
        )

        assert update["published_restaurant_id"] == str(restaurant.id)
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is not None
        assert row.name == "Joe's Pizza"

    async def test_writes_full_menu_tree_including_dishes(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        proposed_change = await _pending_proposed_change(db_session, restaurant)
        node = build_publish_node(db_session)

        await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "structured_json": restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
            }
        )

        expected_dish_id = restaurant.menus[0].categories[0].dishes[0].id
        row = await db_session.get(DishRow, expected_dish_id)
        assert row is not None
        assert row.name == "Margherita Pizza"

    async def test_marks_proposed_change_published(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        proposed_change = await _pending_proposed_change(db_session, restaurant)
        node = build_publish_node(db_session)

        await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "structured_json": restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
            }
        )

        updated = await ProposedChangeRepository(db_session).get_by_id(proposed_change.id)
        assert updated.status == ProposedChangeStatus.PUBLISHED

    async def test_writes_publish_audit_row(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        proposed_change = await _pending_proposed_change(db_session, restaurant)
        node = build_publish_node(db_session)

        await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "structured_json": restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
            }
        )

        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.PROPOSED_CHANGE_PUBLISH,
                AuditLog.entity_type == AuditEntityType.PROPOSED_CHANGE,
                AuditLog.entity_id == str(proposed_change.id),
            )
        )
        assert rows.scalar_one() is not None

    async def test_marks_agent_run_succeeded(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=restaurant.id
        )
        proposed_change = await _pending_proposed_change(db_session, restaurant, agent_run_id=run.id)
        node = build_publish_node(db_session)

        await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "structured_json": restaurant.model_dump(mode="json"),
                "proposed_change_id": str(proposed_change.id),
                "agent_run_id": str(run.id),
            }
        )

        run_row = await db_session.get(AgentRun, run.id)
        assert run_row.status.value == "succeeded"


class TestRefusesUnapprovedData:
    """The core "do not allow unapproved data into production tables"
    guarantee, tested directly at the node level (not just via graph
    routing) — a routing bug elsewhere in the graph must not turn into
    an unapproved write, so this node re-checks for itself."""

    async def test_missing_approval_status_does_not_write(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        node = build_publish_node(db_session)

        update = await node({"structured_json": restaurant.model_dump(mode="json")})

        assert "published_restaurant_id" not in update
        assert len(update["errors"]) == 1
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None

    async def test_rejected_status_does_not_write(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        node = build_publish_node(db_session)

        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.REJECTED,
                "structured_json": restaurant.model_dump(mode="json"),
            }
        )

        assert "published_restaurant_id" not in update
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None

    async def test_pending_status_does_not_write(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        node = build_publish_node(db_session)

        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.PENDING,
                "structured_json": restaurant.model_dump(mode="json"),
            }
        )

        assert "published_restaurant_id" not in update
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None

    async def test_approved_status_without_structured_json_reports_an_error(self, db_session) -> None:
        node = build_publish_node(db_session)

        update = await node({"human_approval_status": ProposedChangeStatus.APPROVED})

        assert "published_restaurant_id" not in update
        assert len(update["errors"]) == 1

    async def test_approved_status_without_proposed_change_id_reports_an_error(self, db_session) -> None:
        restaurant = _restaurant_with_a_dish()
        node = build_publish_node(db_session)

        update = await node(
            {
                "human_approval_status": ProposedChangeStatus.APPROVED,
                "structured_json": restaurant.model_dump(mode="json"),
            }
        )

        assert "published_restaurant_id" not in update
        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None
