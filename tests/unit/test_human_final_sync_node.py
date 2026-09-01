"""Unit tests for the reviewer workflow's Human Final Sync node — the
graph's human-in-the-loop pause/resume point, mirroring
tests/unit/test_human_review_node.py's structure exactly since the
underlying interrupt/resume/idempotency mechanics are identical (see
nodes/human_final_sync.py's docstring for why this reuses that design
rather than reimplementing it). Run against a real Postgres transaction
(db_session) and a real Postgres-backed checkpointer (checkpointer
fixture, tests/conftest.py) — an in-memory checkpointer wouldn't prove
anything about durability.

The one behavioral difference from the collector workflow's equivalent
test file: approval here publishes an UPDATE to an already-published
restaurant, not a brand-new insert, so TestResume seeds a production
restaurant row first.
"""

import uuid
from decimal import Decimal

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy import select

from core.schemas.diff import DeltaOp, FieldDelta, JSONDelta
from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.proposed_change import ProposedChangeStatus
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.models.proposed_change import ProposedChange
from database.repositories.restaurant_repository import RestaurantRepository
from workflows.reviewer_workflow.nodes.human_final_sync import build_human_final_sync_node
from workflows.reviewer_workflow.nodes.publish import build_publish_node
from workflows.reviewer_workflow.state import ReviewerState

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


def _build_sync_publish_graph(db_session, checkpointer):
    graph = StateGraph(ReviewerState)
    graph.add_node("human_final_sync", build_human_final_sync_node(db_session))
    graph.add_node("publish", build_publish_node(db_session))
    graph.add_edge(START, "human_final_sync")

    def route(state):
        return "publish" if state.get("human_approval_status") == ProposedChangeStatus.APPROVED else END

    graph.add_conditional_edges("human_final_sync", route, {"publish": "publish", END: END})
    graph.add_edge("publish", END)
    return graph.compile(checkpointer=checkpointer)


def _initial_state(restaurant: Restaurant, agent_run_id: str) -> dict:
    return {
        "restaurant": restaurant,
        "validated_structured_json": restaurant.model_dump(mode="json"),
        "validation_result": {"is_valid": True, "issues": []},
        "delta": JSONDelta(fields=[FieldDelta(path="name", op=DeltaOp.CHANGED, old_value="a", new_value="b")]),
        "agent_run_id": agent_run_id,
    }


def _thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def _seed_published_restaurant(db_session, restaurant: Restaurant) -> None:
    await RestaurantRepository(db_session).persist_tree(restaurant)
    await db_session.flush()


class TestPauses:
    async def test_first_invoke_returns_an_interrupt(self, db_session, checkpointer) -> None:
        graph = _build_sync_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        result = await graph.ainvoke(
            _initial_state(_restaurant_with_a_dish(), agent_run_id), _thread_config(agent_run_id)
        )

        assert "__interrupt__" in result

    async def test_interrupt_payload_includes_delta(self, db_session, checkpointer) -> None:
        graph = _build_sync_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        result = await graph.ainvoke(
            _initial_state(_restaurant_with_a_dish(), agent_run_id), _thread_config(agent_run_id)
        )

        payload = result["__interrupt__"][0].value
        assert payload["delta"]["fields"][0]["path"] == "name"


class TestCreatesProposedChangeOnPause:
    async def test_creates_exactly_one_pending_proposed_change(self, db_session, checkpointer) -> None:
        graph = _build_sync_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        await graph.ainvoke(_initial_state(_restaurant_with_a_dish(), agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(select(ProposedChange).where(ProposedChange.thread_id == agent_run_id))
        records = rows.scalars().all()
        assert len(records) == 1
        assert records[0].status == ProposedChangeStatus.PENDING


class TestResume:
    async def test_resume_with_approve_updates_the_published_restaurant(self, db_session, checkpointer) -> None:
        restaurant = _restaurant_with_a_dish()
        await _seed_published_restaurant(db_session, restaurant)

        graph = _build_sync_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        updated = restaurant.model_copy(update={"name": "Joe's Pizza (Updated)"})
        await graph.ainvoke(_initial_state(updated, agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(select(ProposedChange).where(ProposedChange.thread_id == agent_run_id))
        record = rows.scalar_one()

        decision = {"action": "approve", "proposed_change_id": str(record.id)}
        result = await graph.ainvoke(Command(resume=decision), _thread_config(agent_run_id))
        await db_session.commit()

        assert result.get("published_restaurant_id") == str(restaurant.id)

        from database.models.restaurant import Restaurant as RestaurantRow

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row.name == "Joe's Pizza (Updated)"

    async def test_resume_with_reject_leaves_the_published_restaurant_untouched(
        self, db_session, checkpointer
    ) -> None:
        restaurant = _restaurant_with_a_dish()
        await _seed_published_restaurant(db_session, restaurant)

        graph = _build_sync_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        updated = restaurant.model_copy(update={"name": "Should Not Publish"})
        await graph.ainvoke(_initial_state(updated, agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(select(ProposedChange).where(ProposedChange.thread_id == agent_run_id))
        record = rows.scalar_one()

        decision = {"action": "reject", "proposed_change_id": str(record.id)}
        result = await graph.ainvoke(Command(resume=decision), _thread_config(agent_run_id))
        await db_session.commit()

        assert "published_restaurant_id" not in result

        from database.models.restaurant import Restaurant as RestaurantRow

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row.name == "Joe's Pizza"


class TestIdempotentRecordCreation:
    async def test_exactly_one_proposed_change_survives_a_full_pause_and_resume_cycle(
        self, db_session, checkpointer
    ) -> None:
        restaurant = _restaurant_with_a_dish()
        await _seed_published_restaurant(db_session, restaurant)

        graph = _build_sync_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        await graph.ainvoke(_initial_state(restaurant, agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(select(ProposedChange).where(ProposedChange.thread_id == agent_run_id))
        record = rows.scalar_one()

        decision = {"action": "approve", "proposed_change_id": str(record.id)}
        await graph.ainvoke(Command(resume=decision), _thread_config(agent_run_id))
        await db_session.commit()

        rows_after = await db_session.execute(
            select(ProposedChange).where(ProposedChange.thread_id == agent_run_id)
        )
        assert len(rows_after.scalars().all()) == 1


class TestFailsClosedWithoutInput:
    async def test_missing_agent_run_id_reports_an_error(self, db_session, checkpointer) -> None:
        node = build_human_final_sync_node(db_session)
        restaurant = _restaurant_with_a_dish()

        update = await node(
            {
                "restaurant": restaurant,
                "validated_structured_json": restaurant.model_dump(mode="json"),
                "validation_result": {"is_valid": True, "issues": []},
            }
        )

        assert "proposed_change_id" not in update
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "human_final_sync"
