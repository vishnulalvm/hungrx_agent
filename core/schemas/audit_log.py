import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from core.schemas.audit import AuditAction, AuditEntityType


class AuditLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: AuditAction
    entity_type: AuditEntityType
    entity_id: str
    actor_id: uuid.UUID | None
    actor_email: str | None
    old_values: dict[str, Any] | None
    new_values: dict[str, Any] | None
    agent_run_id: str | None
    created_at: datetime
