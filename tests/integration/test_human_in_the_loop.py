"""End-to-end integration tests for the human-in-the-loop admin review
API — pending reviews, review detail, approve, reject, edit-then-approve
— exercised through the real FastAPI app (apps/api/app/routers/v1/admin/
router.py) against a real Postgres-backed LangGraph checkpointer, proving
the whole pause -> HTTP request -> resume -> publish/reject cycle works
across what are, in production, genuinely separate requests.

Unlike most integration tests in this suite, this file overrides
`get_settings` (not just `get_db_session`) on the app, because
ReviewService builds its own checkpointer/graph from `Settings.database_url`
(apps/api/app/services/review_service.py) — without the override, it
would resolve against the dev database's checkpoint tables instead of
TEST_DATABASE_URL, which would silently pass or fail for the wrong
reason depending on what happens to be in the dev database.
"""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.settings import Settings
from core.schemas.menu import Dish, Menu, MenuCategory
from core.schemas.proposed_change import ProposedChangeStatus
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.models.restaurant import Restaurant as RestaurantRow
from database.models.user import User
from tests.conftest import TEST_DATABASE_URL, auth_headers, login
from workflows.collector_workflow.nodes.human_review import build_human_review_node
from workflows.collector_workflow.nodes.publish import build_publish_node
from workflows.collector_workflow.state import CollectorState

pytestmark = pytest.mark.asyncio


def _test_settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL)


@pytest_asyncio.fixture
async def review_api_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Same as the shared `app_client` fixture but also overrides
    get_settings so ReviewService's checkpointer/graph resolve against
    TEST_DATABASE_URL — see module docstring."""
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


def _restaurant(name: str = "Joe's Pizza") -> Restaurant:
    dish = Dish(category_id=uuid.uuid4(), name="Margherita Pizza", price=Decimal("12.99"))
    category = MenuCategory(name="Pizzas", dishes=[dish])
    menu = Menu(categories=[category])
    return Restaurant(
        name=name,
        locations=[RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")],
        menus=[menu],
    )


async def _pause_a_review(db_session: AsyncSession, checkpointer, restaurant: Restaurant) -> str:
    """Runs a minimal human_review->publish graph up to its interrupt,
    using the same TEST_DATABASE_URL-backed checkpointer the API's
    ReviewService will resume against, and returns the agent_run_id
    (== thread_id) so a test can then act on it through the real HTTP
    endpoints. This stands in for the earlier pipeline stages
    (source_authority/extraction/multimodal_translation/
    deterministic_validation), which are already covered by their own
    dedicated test files — this file's job is the human-in-the-loop
    boundary and the admin API itself, not re-proving the upstream
    pipeline."""
    graph_builder = StateGraph(CollectorState)
    graph_builder.add_node("human_review", build_human_review_node(db_session))
    graph_builder.add_node("publish", build_publish_node(db_session))
    graph_builder.add_edge(START, "human_review")

    def route(state):
        return "publish" if state.get("human_approval_status") == ProposedChangeStatus.APPROVED else END

    graph_builder.add_conditional_edges("human_review", route, {"publish": "publish", END: END})
    graph_builder.add_edge("publish", END)
    graph = graph_builder.compile(checkpointer=checkpointer)

    agent_run_id = str(uuid.uuid4())
    initial_state = {
        "restaurant": restaurant,
        "structured_json": restaurant.model_dump(mode="json"),
        "validation_result": {"is_valid": True, "issues": []},
        "agent_run_id": agent_run_id,
    }
    await graph.ainvoke(initial_state, {"configurable": {"thread_id": agent_run_id}})
    await db_session.commit()
    return agent_run_id


async def _proposed_change_id_for_thread(db_session: AsyncSession, thread_id: str) -> str:
    from sqlalchemy import select

    from database.models.proposed_change import ProposedChange

    rows = await db_session.execute(select(ProposedChange).where(ProposedChange.thread_id == thread_id))
    return str(rows.scalar_one().id)


class TestPendingReviewsList:
    async def test_lists_a_paused_review(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        restaurant = _restaurant()
        await _pause_a_review(db_session, checkpointer, restaurant)

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        response = await review_api_client.get("/api/v1/admin/reviews", headers=auth_headers(token))

        assert response.status_code == 200
        entity_ids = [row["entity_id"] for row in response.json()]
        assert str(restaurant.id) in entity_ids

    async def test_requires_review_read_permission(self, review_api_client) -> None:
        response = await review_api_client.get("/api/v1/admin/reviews")
        assert response.status_code == 401


class TestReviewDetail:
    async def test_returns_structured_json_and_validation_result(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        restaurant = _restaurant()
        thread_id = await _pause_a_review(db_session, checkpointer, restaurant)
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        response = await review_api_client.get(
            f"/api/v1/admin/reviews/{proposed_change_id}", headers=auth_headers(token)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["structured_json"]["name"] == "Joe's Pizza"
        assert body["validation_result"]["is_valid"] is True
        assert body["status"] == "pending"

    async def test_unknown_id_returns_404(
        self, review_api_client, reviewer_user: User, user_password: str
    ) -> None:
        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        response = await review_api_client.get(
            f"/api/v1/admin/reviews/{uuid.uuid4()}", headers=auth_headers(token)
        )
        assert response.status_code == 404


class TestApprove:
    async def test_approve_publishes_the_restaurant(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        restaurant = _restaurant()
        thread_id = await _pause_a_review(db_session, checkpointer, restaurant)
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        response = await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/approve",
            json={"reason": "looks correct"},
            headers=auth_headers(token),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "published"
        assert body["published_restaurant_id"] == str(restaurant.id)

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is not None
        assert row.name == "Joe's Pizza"

    async def test_approve_writes_an_audit_row(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        from sqlalchemy import select

        from core.schemas.audit import AuditAction, AuditEntityType
        from database.models.audit_log import AuditLog

        restaurant = _restaurant()
        thread_id = await _pause_a_review(db_session, checkpointer, restaurant)
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/approve", json={}, headers=auth_headers(token)
        )

        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.PROPOSED_CHANGE_APPROVE,
                AuditLog.entity_type == AuditEntityType.PROPOSED_CHANGE,
                AuditLog.entity_id == proposed_change_id,
            )
        )
        entry = rows.scalar_one()
        assert entry.actor_id == reviewer_user.id

    async def test_double_approve_is_rejected(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        restaurant = _restaurant()
        thread_id = await _pause_a_review(db_session, checkpointer, restaurant)
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        first = await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/approve", json={}, headers=auth_headers(token)
        )
        assert first.status_code == 200

        second = await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/approve", json={}, headers=auth_headers(token)
        )
        assert second.status_code == 409

    async def test_viewer_cannot_approve(
        self, review_api_client, db_session, checkpointer, viewer_user: User, user_password: str
    ) -> None:
        restaurant = _restaurant()
        thread_id = await _pause_a_review(db_session, checkpointer, restaurant)
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        token = (await login(review_api_client, email=viewer_user.email, password=user_password))[
            "access_token"
        ]
        response = await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/approve", json={}, headers=auth_headers(token)
        )
        assert response.status_code == 403

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None


class TestReject:
    async def test_reject_does_not_publish(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        restaurant = _restaurant()
        thread_id = await _pause_a_review(db_session, checkpointer, restaurant)
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        response = await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/reject",
            json={"reason": "menu data looks wrong"},
            headers=auth_headers(token),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None

    async def test_reject_writes_an_audit_row(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        from sqlalchemy import select

        from core.schemas.audit import AuditAction, AuditEntityType
        from database.models.audit_log import AuditLog

        restaurant = _restaurant()
        thread_id = await _pause_a_review(db_session, checkpointer, restaurant)
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/reject",
            json={"reason": "bad data"},
            headers=auth_headers(token),
        )

        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.PROPOSED_CHANGE_REJECT,
                AuditLog.entity_type == AuditEntityType.PROPOSED_CHANGE,
                AuditLog.entity_id == proposed_change_id,
            )
        )
        assert rows.scalar_one() is not None


class TestEditThenApprove:
    async def test_publishes_edited_data_not_original(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        restaurant = _restaurant()
        thread_id = await _pause_a_review(db_session, checkpointer, restaurant)
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        edited = restaurant.model_dump(mode="json")
        edited["name"] = "Corrected Pizza Name"

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        response = await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/edit-approve",
            json={"edited_structured_json": edited, "reason": "fixed the name"},
            headers=auth_headers(token),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "published"

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row.name == "Corrected Pizza Name"

    async def test_writes_both_edit_and_approve_audit_rows(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        from sqlalchemy import select

        from core.schemas.audit import AuditAction, AuditEntityType
        from database.models.audit_log import AuditLog

        restaurant = _restaurant()
        thread_id = await _pause_a_review(db_session, checkpointer, restaurant)
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        edited = restaurant.model_dump(mode="json")
        edited["name"] = "Corrected Pizza Name"

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/edit-approve",
            json={"edited_structured_json": edited},
            headers=auth_headers(token),
        )

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.entity_id == proposed_change_id)
        )
        actions = {row.action for row in rows.scalars().all()}
        assert AuditAction.PROPOSED_CHANGE_EDIT in actions
        assert AuditAction.PROPOSED_CHANGE_APPROVE in actions


class TestUnapprovedDataNeverReachesProductionTables:
    """The end-to-end version of the guarantee unit-tested directly in
    tests/unit/test_publish_node.py — here proven through the real HTTP
    API and a real paused graph, not just the node function in
    isolation."""

    async def test_a_review_that_is_never_acted_on_writes_nothing(
        self, review_api_client, db_session, checkpointer
    ) -> None:
        restaurant = _restaurant()
        await _pause_a_review(db_session, checkpointer, restaurant)

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None

    async def test_rejected_review_leaves_no_restaurant_row_even_after_the_attempt(
        self, review_api_client, db_session, checkpointer, reviewer_user: User, user_password: str
    ) -> None:
        restaurant = _restaurant()
        thread_id = await _pause_a_review(db_session, checkpointer, restaurant)
        proposed_change_id = await _proposed_change_id_for_thread(db_session, thread_id)

        token = (await login(review_api_client, email=reviewer_user.email, password=user_password))[
            "access_token"
        ]
        await review_api_client.post(
            f"/api/v1/admin/reviews/{proposed_change_id}/reject", json={}, headers=auth_headers(token)
        )

        row = await db_session.get(RestaurantRow, restaurant.id)
        assert row is None
