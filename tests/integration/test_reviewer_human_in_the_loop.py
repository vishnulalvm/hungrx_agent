"""Integration coverage for the reviewer workflow's human-in-the-loop
boundary going through the real admin review API (ReviewService), not
just the graph directly — specifically proving the bug this task's
review fixed: ReviewService._resume used to always build the collector
workflow's graph to resume a paused run, which would have been wrong
for anything the reviewer workflow paused (different node names/
topology entirely). This file seeds a ProposedChange the way the
reviewer workflow actually produces one (an AgentRun with
workflow_type=REVIEWER, referenced by ProposedChange.agent_run_id) and
proves the admin API's approve/reject endpoints correctly resume the
REVIEWER graph and reach the reviewer workflow's own publish node — an
UPDATE to an already-published restaurant, not an insert of a new one.

Same TEST_DATABASE_URL settings-override pattern as
tests/integration/test_human_in_the_loop.py — see that file's module
docstring for why get_settings (not just get_db_session) has to be
overridden.
"""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.settings import Settings
from core.schemas.agent_run import AgentWorkflowType
from core.schemas.diff import DeltaOp, FieldDelta, JSONDelta
from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.proposed_change import ProposedChangeStatus
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.models.proposed_change import ProposedChange
from database.models.restaurant import Restaurant as RestaurantRow
from database.models.user import User
from database.repositories.agent_run_repository import AgentRunRepository
from database.repositories.restaurant_repository import RestaurantRepository
from tests.conftest import TEST_DATABASE_URL, auth_headers, login
from workflows.reviewer_workflow.nodes.human_final_sync import build_human_final_sync_node
from workflows.reviewer_workflow.nodes.publish import build_publish_node
from workflows.reviewer_workflow.state import ReviewerState

pytestmark = pytest.mark.asyncio


def _test_settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL)


@pytest_asyncio.fixture
async def review_api_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    from apps.api.app.dependencies.db import get_db_session
    from apps.api.app.dependencies.settings import get_settings as get_settings_dep
    from apps.api.app.main import app
    from core.config.settings import get_settings as get_settings_module

    async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    test_settings = _test_settings()
    app.dependency_overrides[get_db_session] = _override_get_db_session
    app.dependency_overrides[get_settings_dep] = lambda: test_settings
    app.dependency_overrides[get_settings_module] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()


def _restaurant_with_a_dish(*, name: str = "Joe's Pizza") -> Restaurant:
    dish = Dish(category_id=uuid.uuid4(), name="Margherita Pizza", price=Decimal("12.99"))
    category = MenuCategory(name="Pizzas", dishes=[dish])
    menu = Menu(categories=[category])
    return Restaurant(
        name=name,
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
        menus=[menu],
    )


async def _pause_a_reviewer_change(
    db_session: AsyncSession, checkpointer, restaurant: Restaurant, *, updated_description: str
) -> str:
    """Seeds a published restaurant, then runs a minimal
    human_final_sync -> publish reviewer-workflow graph up to its
    interrupt, using a real AgentRun (workflow_type=REVIEWER) so
    ReviewService's dispatch logic has something real to key off of.
    Returns the thread_id (== agent_run_id)."""
    await RestaurantRepository(db_session).persist_tree(restaurant)

    run = await AgentRunRepository(db_session).create(
        workflow_type=AgentWorkflowType.REVIEWER, restaurant_id=restaurant.id
    )
    thread_id = str(run.id)

    graph_builder = StateGraph(ReviewerState)
    graph_builder.add_node("human_final_sync", build_human_final_sync_node(db_session))
    graph_builder.add_node("publish", build_publish_node(db_session))
    graph_builder.add_edge(START, "human_final_sync")

    def route(state):
        return "publish" if state.get("human_approval_status") == ProposedChangeStatus.APPROVED else END

    graph_builder.add_conditional_edges("human_final_sync", route, {"publish": "publish", END: END})
    graph_builder.add_edge("publish", END)
    graph = graph_builder.compile(checkpointer=checkpointer)

    updated = restaurant.model_copy(update={"description": updated_description})
    delta = JSONDelta(
        fields=[
            FieldDelta(path="description", op=DeltaOp.CHANGED, old_value=None, new_value=updated_description)
        ]
    )
    initial_state = {
        "restaurant": restaurant,
        "validated_structured_json": updated.model_dump(mode="json"),
        "validation_result": {"is_valid": True, "issues": []},
        "delta": delta,
        "agent_run_id": thread_id,
    }
    await graph.ainvoke(initial_state, {"configurable": {"thread_id": thread_id}})
    await db_session.commit()
    return thread_id


async def _proposed_change_id_for_thread(db_session: AsyncSession, thread_id: str) -> str:
    rows = await db_session.execute(select(ProposedChange).where(ProposedChange.thread_id == thread_id))
    return str(rows.scalar_one().id)


class TestApproveResumesTheReviewerGraph:
    async def test_approve_publishes_via_the_reviewer_workflows_own_publish_node(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        restaurant = _restaurant_with_a_dish()
        thread_id = await _pause_a_reviewer_change(
            db_session, checkpointer, restaurant, updated_description="A brand new description."
        )
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        response = await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/approve",
            json={"reason": "looks right"},
            headers=auth_headers(token),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "published"
        assert body["published_restaurant_id"] == str(restaurant.id)
        assert body["errors"] == []

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row.description == "A brand new description."

    async def test_reject_does_not_publish_and_leaves_production_untouched(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        restaurant = _restaurant_with_a_dish()
        thread_id = await _pause_a_reviewer_change(
            db_session, checkpointer, restaurant, updated_description="Should not be published."
        )
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        response = await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/reject",
            json={"reason": "not accurate"},
            headers=auth_headers(token),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row.description is None  # original published state, untouched
