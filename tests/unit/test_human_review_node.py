"""Unit tests for the collector workflow's Human Review node (Agent 5) —
the graph's human-in-the-loop pause/resume point. Run against a real
Postgres transaction (db_session) for ProposedChange/AuditLog and a real
Postgres-backed checkpointer (checkpointer fixture, tests/conftest.py)
for the actual LangGraph interrupt/resume — an in-memory checkpointer
wouldn't prove anything about durability, which is the entire point of
this node's design.

Covers: the graph actually pauses at the interrupt, resuming with a
decision continues the run, idempotent ProposedChange creation across a
replayed resume (the bug this design specifically guards against — see
the node's docstring), and that each decision (approve/reject/
edit_then_approve) routes correctly.
"""

import uuid
from datetime import datetime, timezone

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy import select

from core.schemas.proposed_change import ProposedChangeStatus
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.models.proposed_change import ProposedChange
from workflows.collector_workflow.nodes.human_review import build_human_review_node
from workflows.collector_workflow.nodes.publish import build_publish_node
from workflows.collector_workflow.state import CollectorState

pytestmark = pytest.mark.asyncio


def _restaurant() -> Restaurant:
    return Restaurant(
        name="Joe's Pizza",
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
    )


def _build_review_publish_graph(db_session, checkpointer):
    graph = StateGraph(CollectorState)
    graph.add_node("human_review", build_human_review_node(db_session))
    graph.add_node("publish", build_publish_node(db_session))
    graph.add_edge(START, "human_review")

    def route(state):
        return "publish" if state.get("human_approval_status") == ProposedChangeStatus.APPROVED else END

    graph.add_conditional_edges("human_review", route, {"publish": "publish", END: END})
    graph.add_edge("publish", END)
    return graph.compile(checkpointer=checkpointer)


def _initial_state(restaurant: Restaurant, agent_run_id: str) -> dict:
    return {
        "restaurant": restaurant,
        "structured_json": restaurant.model_dump(mode="json"),
        "validation_result": {"is_valid": True, "issues": []},
        "agent_run_id": agent_run_id,
    }


def _thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


class TestPauses:
    async def test_first_invoke_returns_an_interrupt(self, db_session, checkpointer) -> None:
        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        result = await graph.ainvoke(_initial_state(_restaurant(), agent_run_id), _thread_config(agent_run_id))

        assert "__interrupt__" in result
        assert len(result["__interrupt__"]) == 1

    async def test_interrupt_payload_includes_proposed_change_id(self, db_session, checkpointer) -> None:
        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        result = await graph.ainvoke(_initial_state(_restaurant(), agent_run_id), _thread_config(agent_run_id))

        payload = result["__interrupt__"][0].value
        assert "proposed_change_id" in payload
        uuid.UUID(payload["proposed_change_id"])  # does not raise

    async def test_pause_does_not_run_publish(self, db_session, checkpointer) -> None:
        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        await graph.ainvoke(_initial_state(_restaurant(), agent_run_id), _thread_config(agent_run_id))

        state_snapshot = await graph.aget_state(_thread_config(agent_run_id))
        assert "publish" not in state_snapshot.next
        assert "human_review" in state_snapshot.next


class TestCreatesProposedChangeOnPause:
    async def test_creates_exactly_one_pending_proposed_change(self, db_session, checkpointer) -> None:
        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        await graph.ainvoke(_initial_state(_restaurant(), agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(
            select(ProposedChange).where(ProposedChange.thread_id == agent_run_id)
        )
        records = rows.scalars().all()
        assert len(records) == 1
        assert records[0].status == ProposedChangeStatus.PENDING

    async def test_proposed_change_carries_structured_json_and_validation_result(
        self, db_session, checkpointer
    ) -> None:
        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        restaurant = _restaurant()
        await graph.ainvoke(_initial_state(restaurant, agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(
            select(ProposedChange).where(ProposedChange.thread_id == agent_run_id)
        )
        record = rows.scalar_one()
        assert record.structured_json["name"] == "Joe's Pizza"
        assert record.validation_result["is_valid"] is True


class TestResume:
    async def test_resume_with_approve_routes_to_publish(self, db_session, checkpointer) -> None:
        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        restaurant = _restaurant()
        await graph.ainvoke(_initial_state(restaurant, agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(
            select(ProposedChange).where(ProposedChange.thread_id == agent_run_id)
        )
        record = rows.scalar_one()

        decision = {"action": "approve", "proposed_change_id": str(record.id)}
        result = await graph.ainvoke(Command(resume=decision), _thread_config(agent_run_id))
        await db_session.commit()

        assert result.get("published_restaurant_id") == str(restaurant.id)
        assert result.get("errors", []) == []

    async def test_resume_with_reject_does_not_publish(self, db_session, checkpointer) -> None:
        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        restaurant = _restaurant()
        await graph.ainvoke(_initial_state(restaurant, agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(
            select(ProposedChange).where(ProposedChange.thread_id == agent_run_id)
        )
        record = rows.scalar_one()

        decision = {"action": "reject", "proposed_change_id": str(record.id)}
        result = await graph.ainvoke(Command(resume=decision), _thread_config(agent_run_id))
        await db_session.commit()

        assert "published_restaurant_id" not in result

        from database.models.restaurant import Restaurant as RestaurantRow

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None

    async def test_resume_with_edit_then_approve_publishes_edited_data(self, db_session, checkpointer) -> None:
        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        restaurant = _restaurant()
        await graph.ainvoke(_initial_state(restaurant, agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(
            select(ProposedChange).where(ProposedChange.thread_id == agent_run_id)
        )
        record = rows.scalar_one()

        edited = dict(record.structured_json)
        edited["name"] = "Edited Pizza Palace"
        decision = {
            "action": "edit_then_approve",
            "proposed_change_id": str(record.id),
            "edited_structured_json": edited,
        }
        result = await graph.ainvoke(Command(resume=decision), _thread_config(agent_run_id))
        await db_session.commit()

        assert result.get("published_restaurant_id") == str(restaurant.id)

        from database.models.restaurant import Restaurant as RestaurantRow

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row.name == "Edited Pizza Palace"

    async def test_resume_with_unrecognized_action_reports_an_error(self, db_session, checkpointer) -> None:
        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        await graph.ainvoke(_initial_state(_restaurant(), agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        decision = {"action": "do_something_weird"}
        result = await graph.ainvoke(Command(resume=decision), _thread_config(agent_run_id))
        await db_session.commit()

        assert any(error["node"] == "human_review" for error in result.get("errors", []))
        assert "published_restaurant_id" not in result


class TestIdempotentRecordCreation:
    """The specific bug this design guards against: LangGraph replays
    the human_review node function from the top on resume (same input
    state as before the interrupt), so a naive "create if not already on
    state" check would create a second ProposedChange row on every
    resume. See nodes/human_review.py's docstring for the full
    explanation; get_by_thread_id is what makes creation idempotent
    instead."""

    async def test_exactly_one_proposed_change_survives_a_full_pause_and_resume_cycle(
        self, db_session, checkpointer
    ) -> None:
        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        restaurant = _restaurant()
        await graph.ainvoke(_initial_state(restaurant, agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(
            select(ProposedChange).where(ProposedChange.thread_id == agent_run_id)
        )
        record = rows.scalar_one()

        decision = {"action": "approve", "proposed_change_id": str(record.id)}
        await graph.ainvoke(Command(resume=decision), _thread_config(agent_run_id))
        await db_session.commit()

        rows_after = await db_session.execute(
            select(ProposedChange).where(ProposedChange.thread_id == agent_run_id)
        )
        assert len(rows_after.scalars().all()) == 1

    async def test_no_duplicate_proposed_change_create_audit_row(self, db_session, checkpointer) -> None:
        from sqlalchemy import select as sa_select

        from core.schemas.audit import AuditAction, AuditEntityType
        from database.models.audit_log import AuditLog

        graph = _build_review_publish_graph(db_session, checkpointer)
        agent_run_id = str(uuid.uuid4())
        restaurant = _restaurant()
        await graph.ainvoke(_initial_state(restaurant, agent_run_id), _thread_config(agent_run_id))
        await db_session.commit()

        rows = await db_session.execute(
            select(ProposedChange).where(ProposedChange.thread_id == agent_run_id)
        )
        record = rows.scalar_one()

        decision = {"action": "approve", "proposed_change_id": str(record.id)}
        await graph.ainvoke(Command(resume=decision), _thread_config(agent_run_id))
        await db_session.commit()

        audit_rows = await db_session.execute(
            sa_select(AuditLog).where(
                AuditLog.action == AuditAction.PROPOSED_CHANGE_CREATE,
                AuditLog.entity_type == AuditEntityType.PROPOSED_CHANGE,
                AuditLog.entity_id == str(record.id),
            )
        )
        assert len(audit_rows.scalars().all()) == 1


class TestFailsClosedWithoutInput:
    async def test_missing_agent_run_id_reports_an_error(self, db_session, checkpointer) -> None:
        node = build_human_review_node(db_session)
        restaurant = _restaurant()

        update = await node(
            {
                "restaurant": restaurant,
                "structured_json": restaurant.model_dump(mode="json"),
                "validation_result": {"is_valid": True, "issues": []},
            }
        )

        assert "proposed_change_id" not in update
        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "human_review"

    async def test_missing_structured_json_reports_an_error(self, db_session, checkpointer) -> None:
        node = build_human_review_node(db_session)
        restaurant = _restaurant()

        update = await node({"restaurant": restaurant, "agent_run_id": str(uuid.uuid4())})

        assert "proposed_change_id" not in update
        assert len(update["errors"]) == 1
