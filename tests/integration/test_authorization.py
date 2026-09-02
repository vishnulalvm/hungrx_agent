"""Integration tests proving each role tier gets exactly the access the
permission matrix says it should, exercised through the real protected
admin endpoints (apps/api/app/routers/v1/admin/router.py)."""

import pytest
from httpx import AsyncClient

from database.models.user import User
from tests.conftest import auth_headers, login

pytestmark = pytest.mark.asyncio


async def _token_for(app_client: AsyncClient, user: User, password: str) -> str:
    tokens = await login(app_client, email=user.email, password=password)
    return tokens["access_token"]


class TestViewerPermissions:
    """VIEWER: read-only across the board."""

    async def test_viewer_can_read_restaurants(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.get("/api/v1/admin/restaurants", headers=auth_headers(token))
        assert response.status_code == 200

    async def test_viewer_can_read_pending_reviews(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.get("/api/v1/admin/reviews", headers=auth_headers(token))
        assert response.status_code == 200

    async def test_viewer_cannot_approve_review_item(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.post(
            "/api/v1/admin/reviews/00000000-0000-0000-0000-000000000000/approve",
            json={},
            headers=auth_headers(token),
        )
        assert response.status_code == 403

    async def test_viewer_cannot_trigger_ingestion(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.post(
            "/api/v1/admin/ingestion/trigger",
            json={"name": "Joe's Pizza"},
            headers=auth_headers(token),
        )
        assert response.status_code == 403

    async def test_viewer_cannot_list_users(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, viewer_user, user_password)
        response = await app_client.get("/api/v1/admin/users", headers=auth_headers(token))
        assert response.status_code == 403


class TestReviewerPermissions:
    """REVIEWER: read + act on the review queue, nothing else."""

    async def test_reviewer_can_read_restaurants(
        self, app_client: AsyncClient, reviewer_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, reviewer_user, user_password)
        response = await app_client.get("/api/v1/admin/restaurants", headers=auth_headers(token))
        assert response.status_code == 200

    async def test_reviewer_can_attempt_to_approve_review_item(
        self, app_client: AsyncClient, reviewer_user: User, user_password: str
    ) -> None:
        # Permission granted -> passes the require_permission gate and
        # reaches the handler; a nonexistent id then 404s rather than
        # 403ing, proving REVIEW_WRITE was actually checked and passed.
        token = await _token_for(app_client, reviewer_user, user_password)
        response = await app_client.post(
            "/api/v1/admin/reviews/00000000-0000-0000-0000-000000000000/approve",
            json={},
            headers=auth_headers(token),
        )
        assert response.status_code == 404

    async def test_reviewer_cannot_trigger_ingestion(
        self, app_client: AsyncClient, reviewer_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, reviewer_user, user_password)
        response = await app_client.post(
            "/api/v1/admin/ingestion/trigger",
            json={"name": "Joe's Pizza"},
            headers=auth_headers(token),
        )
        assert response.status_code == 403

    async def test_reviewer_cannot_list_users(
        self, app_client: AsyncClient, reviewer_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, reviewer_user, user_password)
        response = await app_client.get("/api/v1/admin/users", headers=auth_headers(token))
        assert response.status_code == 403


class TestDataManagerPermissions:
    """DATA_MANAGER: full restaurant/ingestion control, but not user mgmt."""

    async def test_data_manager_can_trigger_ingestion(
        self, app_client: AsyncClient, data_manager_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, data_manager_user, user_password)
        response = await app_client.post(
            "/api/v1/admin/ingestion/trigger",
            json={"name": "Joe's Pizza"},
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        assert response.json()["job_id"]

    async def test_data_manager_can_attempt_to_approve_review_item(
        self, app_client: AsyncClient, data_manager_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, data_manager_user, user_password)
        response = await app_client.post(
            "/api/v1/admin/reviews/00000000-0000-0000-0000-000000000000/approve",
            json={},
            headers=auth_headers(token),
        )
        assert response.status_code == 404

    async def test_data_manager_cannot_list_users(
        self, app_client: AsyncClient, data_manager_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, data_manager_user, user_password)
        response = await app_client.get("/api/v1/admin/users", headers=auth_headers(token))
        assert response.status_code == 403


class TestSuperAdminPermissions:
    """SUPER_ADMIN: everything, including user management."""

    async def test_super_admin_can_list_users(
        self, app_client: AsyncClient, super_admin_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, super_admin_user, user_password)
        response = await app_client.get("/api/v1/admin/users", headers=auth_headers(token))
        assert response.status_code == 200

    async def test_super_admin_can_trigger_ingestion(
        self, app_client: AsyncClient, super_admin_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, super_admin_user, user_password)
        response = await app_client.post(
            "/api/v1/admin/ingestion/trigger",
            json={"name": "Joe's Pizza"},
            headers=auth_headers(token),
        )
        assert response.status_code == 200

    async def test_super_admin_can_attempt_to_approve_review_item(
        self, app_client: AsyncClient, super_admin_user: User, user_password: str
    ) -> None:
        token = await _token_for(app_client, super_admin_user, user_password)
        response = await app_client.post(
            "/api/v1/admin/reviews/00000000-0000-0000-0000-000000000000/approve",
            json={},
            headers=auth_headers(token),
        )
        assert response.status_code == 404


class TestUnauthenticatedAccess:
    async def test_all_protected_admin_routes_reject_no_token(self, app_client: AsyncClient) -> None:
        for method, path in [
            ("GET", "/api/v1/admin/restaurants"),
            ("GET", "/api/v1/admin/reviews"),
            ("POST", "/api/v1/admin/reviews/00000000-0000-0000-0000-000000000000/approve"),
            ("POST", "/api/v1/admin/ingestion/trigger"),
            ("GET", "/api/v1/admin/users"),
        ]:
            response = await app_client.request(method, path)
            assert response.status_code == 401, f"{method} {path} did not require auth"
