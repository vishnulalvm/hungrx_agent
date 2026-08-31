"""Roles and permissions shared by the ORM model, JWT claims, and API
schemas. Living in core (not database/ or apps/api/) so both the api and
worker processes — and any future service — agree on one definition."""

import enum


class Role(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    DATA_MANAGER = "DATA_MANAGER"
    REVIEWER = "REVIEWER"
    VIEWER = "VIEWER"


class Permission(str, enum.Enum):
    # Restaurant / menu data
    RESTAURANT_READ = "restaurant:read"
    RESTAURANT_WRITE = "restaurant:write"
    RESTAURANT_DELETE = "restaurant:delete"

    # Review queue (confirming/rejecting AI-collected data)
    REVIEW_READ = "review:read"
    REVIEW_WRITE = "review:write"

    # Ingestion pipeline / crawler control
    INGESTION_TRIGGER = "ingestion:trigger"
    INGESTION_READ = "ingestion:read"

    # LangGraph agent runs
    AGENT_RUN_READ = "agent_run:read"
    AGENT_RUN_TRIGGER = "agent_run:trigger"

    # User management
    USER_READ = "user:read"
    USER_WRITE = "user:write"

    # Audit log
    AUDIT_LOG_READ = "audit_log:read"

    # Platform settings
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"


# Every permission each role holds. Deliberately explicit (no inheritance
# chain) so a permission's presence for a given role is a one-line lookup,
# not something you have to trace through a hierarchy to confirm.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.SUPER_ADMIN: frozenset(Permission),  # everything
    Role.DATA_MANAGER: frozenset(
        {
            Permission.RESTAURANT_READ,
            Permission.RESTAURANT_WRITE,
            Permission.RESTAURANT_DELETE,
            Permission.REVIEW_READ,
            Permission.REVIEW_WRITE,
            Permission.INGESTION_TRIGGER,
            Permission.INGESTION_READ,
            Permission.AGENT_RUN_READ,
            Permission.AGENT_RUN_TRIGGER,
            Permission.AUDIT_LOG_READ,
            Permission.SETTINGS_READ,
        }
    ),
    Role.REVIEWER: frozenset(
        {
            Permission.RESTAURANT_READ,
            Permission.REVIEW_READ,
            Permission.REVIEW_WRITE,
            Permission.INGESTION_READ,
            Permission.AGENT_RUN_READ,
        }
    ),
    Role.VIEWER: frozenset(
        {
            Permission.RESTAURANT_READ,
            Permission.REVIEW_READ,
            Permission.INGESTION_READ,
            Permission.AGENT_RUN_READ,
            Permission.AUDIT_LOG_READ,
        }
    ),
}


def permissions_for_role(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]
