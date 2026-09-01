"""Audit action/entity vocabulary shared by the ORM model, the audit
service, and API schemas — living in core (not database/ or apps/api/) so
every service that needs to write or read audit records agrees on one
definition, the same pattern used for Role/Permission in core.schemas.auth.
"""

import enum


class AuditAction(str, enum.Enum):
    # Restaurant data
    RESTAURANT_CREATE = "restaurant.create"
    RESTAURANT_EDIT = "restaurant.edit"
    RESTAURANT_DELETE = "restaurant.delete"

    # AI / agent pipeline
    AI_EXTRACTION = "ai.extraction"
    AGENT_RUN_TRIGGER = "agent_run.trigger"

    # Proposed changes / review queue
    PROPOSED_CHANGE_CREATE = "proposed_change.create"
    PROPOSED_CHANGE_EDIT = "proposed_change.edit"
    PROPOSED_CHANGE_APPROVE = "proposed_change.approve"
    PROPOSED_CHANGE_REJECT = "proposed_change.reject"
    PROPOSED_CHANGE_PUBLISH = "proposed_change.publish"

    # Security / session events
    LOGIN_SUCCESS = "security.login_success"
    LOGIN_FAILURE = "security.login_failure"
    LOGOUT = "security.logout"
    LOGOUT_ALL = "security.logout_all"
    TOKEN_REFRESH = "security.token_refresh"


class AuditEntityType(str, enum.Enum):
    RESTAURANT = "restaurant"
    PROPOSED_CHANGE = "proposed_change"
    AGENT_RUN = "agent_run"
    USER = "user"
    SESSION = "session"
