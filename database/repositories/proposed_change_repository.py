import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.proposed_change import ProposedChangeEntityType, ProposedChangeStatus
from database.models.proposed_change import Approval, ProposedChange


class ProposedChangeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        entity_type: ProposedChangeEntityType,
        entity_id: uuid.UUID,
        structured_json: dict,
        validation_result: dict,
        agent_run_id: uuid.UUID | None,
        source_id: uuid.UUID | None,
        thread_id: str | None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> ProposedChange:
        record = ProposedChange(
            entity_type=entity_type,
            entity_id=entity_id,
            structured_json=structured_json,
            validation_result=validation_result,
            status=ProposedChangeStatus.PENDING,
            agent_run_id=agent_run_id,
            source_id=source_id,
            thread_id=thread_id,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_by_id(self, proposed_change_id: uuid.UUID) -> ProposedChange | None:
        return await self._session.get(ProposedChange, proposed_change_id)

    async def get_by_thread_id(self, thread_id: str) -> ProposedChange | None:
        """Looks up an existing ProposedChange by its LangGraph thread_id.
        Used by the human_review node to make record-creation idempotent
        across LangGraph's node-replay-on-resume behavior — see that
        node's docstring for why a naive "create if not already on
        state" check is unsafe here."""
        result = await self._session.execute(
            select(ProposedChange).where(ProposedChange.thread_id == thread_id)
        )
        return result.scalar_one_or_none()

    async def get_published_for_entity(self, entity_id: uuid.UUID) -> ProposedChange | None:
        """Looks up an already-PUBLISHED ProposedChange for a given
        entity_id. Used by the publish node to refuse republishing over
        an existing production restaurant — publish always represents a
        new entity, never a silent overwrite; see that node's docstring
        on preserving version/history."""
        result = await self._session.execute(
            select(ProposedChange).where(
                ProposedChange.entity_id == entity_id,
                ProposedChange.status == ProposedChangeStatus.PUBLISHED,
            )
        )
        return result.scalars().first()

    async def list_pending(self, *, limit: int = 100) -> list[ProposedChange]:
        result = await self._session.execute(
            select(ProposedChange)
            .where(ProposedChange.status == ProposedChangeStatus.PENDING)
            .order_by(desc(ProposedChange.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_status(
        self, proposed_change_id: uuid.UUID, *, status: ProposedChangeStatus, structured_json: dict | None = None
    ) -> ProposedChange | None:
        record = await self._session.get(ProposedChange, proposed_change_id)
        if record is None:
            return None
        record.status = status
        if structured_json is not None:
            record.structured_json = structured_json
        await self._session.flush()
        return record


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        proposed_change_id: uuid.UUID,
        decision: ProposedChangeStatus,
        reviewer_user_id: uuid.UUID,
        reason: str | None = None,
    ) -> Approval:
        record = Approval(
            proposed_change_id=proposed_change_id,
            decision=decision,
            reviewer_user_id=reviewer_user_id,
            reason=reason,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_for_proposed_change(self, proposed_change_id: uuid.UUID) -> list[Approval]:
        result = await self._session.execute(
            select(Approval)
            .where(Approval.proposed_change_id == proposed_change_id)
            .order_by(desc(Approval.decided_at))
        )
        return list(result.scalars().all())
