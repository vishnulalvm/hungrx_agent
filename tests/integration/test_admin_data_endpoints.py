"""Integration coverage for the admin dashboard's restaurant listing and
agent-run status endpoints — the two read surfaces built out for
apps/admin-dashboard's Restaurants and Agent Runs pages. Permission-tier
gating for these routes is already covered by test_authorization.py;
this file exercises the actual response shape/behavior (pagination, 404
on an unknown id) through the real HTTP layer.
"""

import uuid

import pytest
from httpx import AsyncClient

from core.schemas.agent_run import AgentWorkflowType
from core.schemas.restaurant import Restaurant, RestaurantLocation
from database.repositories.agent_run_repository import AgentRunRepository
from database.repositories.restaurant_repository import RestaurantRepository
from database.models.user import User
from tests.conftest import auth_headers, login

pytestmark = pytest.mark.asyncio


async def _token_for(app_client: AsyncClient, user: User, password: str) -> str:
    tokens = await login(app_client, email=user.email, password=password)
    return tokens["access_token"]


class TestRestaurantEndpoints:
    async def test_list_restaurants_returns_paginated_summaries(
        self, app_client: AsyncClient, db_session, viewer_user: User, user_password: str
    ) -> None:
        restaurant = Restaurant(
            name="Joe's Pizza",
            locations=[RestaurantLocation(address_line1="1 Main St", city="Austin", country="US")],
        )
        await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.commit()

        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.get("/api/v1/admin/restaurants", headers=auth_headers(token))

        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 1
        assert any(item["name"] == "Joe's Pizza" for item in body["items"])

    async def test_get_restaurant_detail(
        self, app_client: AsyncClient, db_session, viewer_user: User, user_password: str
    ) -> None:
        restaurant = Restaurant(
            name="Anna's Diner",
            locations=[RestaurantLocation(address_line1="2 Main St", city="Dallas", country="US")],
        )
        record = await RestaurantRepository(db_session).persist_tree(restaurant)
        await db_session.commit()

        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.get(
            f"/api/v1/admin/restaurants/{record.id}", headers=auth_headers(token)
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Anna's Diner"

    async def test_get_restaurant_detail_404s_for_unknown_id(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.get(
            f"/api/v1/admin/restaurants/{uuid.uuid4()}", headers=auth_headers(token)
        )
        assert response.status_code == 404


class TestAgentRunEndpoints:
    async def test_list_agent_runs_returns_paginated_results(
        self, app_client: AsyncClient, db_session, viewer_user: User, user_password: str
    ) -> None:
        await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=None
        )
        await db_session.commit()

        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.get("/api/v1/agents/runs", headers=auth_headers(token))

        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 1
        assert body["items"][0]["workflow_type"] == "collector_workflow"

    async def test_get_agent_run_detail(
        self, app_client: AsyncClient, db_session, viewer_user: User, user_password: str
    ) -> None:
        run = await AgentRunRepository(db_session).create(
            workflow_type=AgentWorkflowType.REVIEWER, restaurant_id=None
        )
        await db_session.commit()

        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.get(f"/api/v1/agents/runs/{run.id}", headers=auth_headers(token))

        assert response.status_code == 200
        assert response.json()["id"] == str(run.id)
        assert response.json()["status"] == "running"

    async def test_get_agent_run_404s_for_unknown_id(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.get(f"/api/v1/agents/runs/{uuid.uuid4()}", headers=auth_headers(token))
        assert response.status_code == 404
