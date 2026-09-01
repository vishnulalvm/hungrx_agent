"""Shape of one LangGraph workflow execution. The graph implementation
itself is a separate future task ("do not build the AI workflows yet") —
this only defines what a run record looks like so ProposedChange and the
audit system have something typed to reference."""

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentWorkflowType(str, enum.Enum):
    COLLECTOR = "collector_workflow"
    REVIEWER = "reviewer_workflow"


class AgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    workflow_type: AgentWorkflowType
    restaurant_id: uuid.UUID | None = None
    status: AgentRunStatus = AgentRunStatus.PENDING

    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
