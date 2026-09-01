import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.agent_run import AgentRunStatus, AgentWorkflowType
from database.models.agent_run import AgentRun


class AgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, workflow_type: AgentWorkflowType, restaurant_id: uuid.UUID | None
    ) -> AgentRun:
        record = AgentRun(
            workflow_type=workflow_type,
            restaurant_id=restaurant_id,
            status=AgentRunStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_by_id(self, run_id: uuid.UUID) -> AgentRun | None:
        return await self._session.get(AgentRun, run_id)

    async def mark_succeeded(self, run_id: uuid.UUID) -> None:
        record = await self._session.get(AgentRun, run_id)
        if record is None:
            return
        record.status = AgentRunStatus.SUCCEEDED
        record.completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def mark_failed(self, run_id: uuid.UUID, *, error_message: str) -> None:
        record = await self._session.get(AgentRun, run_id)
        if record is None:
            return
        record.status = AgentRunStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error_message = error_message[:2000]
        await self._session.flush()

    async def update_metrics(self, run_id: uuid.UUID, *, metrics: dict[str, Any]) -> None:
        """Merges `metrics` onto the run's existing metrics dict (shallow
        update — a later call's keys overwrite same-named earlier ones,
        but keys neither call touches survive) rather than replacing it
        outright, since a node calling this partway through a run
        shouldn't erase metrics an earlier node in the same run already
        recorded."""
        record = await self._session.get(AgentRun, run_id)
        if record is None:
            return
        record.metrics = {**record.metrics, **metrics}
        await self._session.flush()
