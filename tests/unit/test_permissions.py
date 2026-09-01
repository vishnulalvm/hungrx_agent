"""Unit tests for the role -> permission matrix in core.schemas.auth."""

import pytest

from core.schemas.auth import ROLE_PERMISSIONS, Permission, Role, permissions_for_role


class TestRolePermissionMatrix:
    def test_every_role_has_an_entry(self) -> None:
        for role in Role:
            assert role in ROLE_PERMISSIONS

    def test_super_admin_has_every_permission(self) -> None:
        assert permissions_for_role(Role.SUPER_ADMIN) == frozenset(Permission)

    def test_viewer_has_no_write_permissions(self) -> None:
        viewer_perms = permissions_for_role(Role.VIEWER)
        write_perms = {p for p in Permission if p.value.endswith(("write", "trigger", "delete"))}
        assert viewer_perms.isdisjoint(write_perms)

    def test_viewer_can_read_restaurants(self) -> None:
        assert Permission.RESTAURANT_READ in permissions_for_role(Role.VIEWER)

    def test_viewer_cannot_write_restaurants(self) -> None:
        assert Permission.RESTAURANT_WRITE not in permissions_for_role(Role.VIEWER)

    def test_reviewer_can_write_reviews_but_not_restaurants(self) -> None:
        perms = permissions_for_role(Role.REVIEWER)
        assert Permission.REVIEW_WRITE in perms
        assert Permission.RESTAURANT_WRITE not in perms

    def test_data_manager_can_write_restaurants_and_trigger_ingestion(self) -> None:
        perms = permissions_for_role(Role.DATA_MANAGER)
        assert Permission.RESTAURANT_WRITE in perms
        assert Permission.INGESTION_TRIGGER in perms

    def test_only_super_admin_can_manage_users(self) -> None:
        for role in Role:
            perms = permissions_for_role(role)
            if role is Role.SUPER_ADMIN:
                assert Permission.USER_WRITE in perms
            else:
                assert Permission.USER_WRITE not in perms

    @pytest.mark.parametrize("role", list(Role))
    def test_permissions_for_role_is_deterministic(self, role: Role) -> None:
        assert permissions_for_role(role) == permissions_for_role(role)
