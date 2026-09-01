"""Business logic for the admin review queue: listing pending
ProposedChanges, reading one in detail, and resuming its paused collector
run with an approve/reject/edit-then-approve decision.

This is the service the admin API's /reviews endpoints
(apps/api/app/routers/v1/admin/router.py) call into. It owns the only
code path that resumes a graph interrupted at
workflows.collector_workflow.nodes.human_review — every decision goes
through `resume_review`, so every decision is auditable and, for
approve/edit_then_approve, ends with either a real production write (via
the graph's publish node) or a clearly reported failure — never a
silent partial state.
"""

import uuid
from typing import Any

from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.config.exceptions import ConflictError, NotFoundError
from core.config.settings import Settings
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.proposed_change import ProposedChangeStatus
from database.models.proposed_change import ProposedChange
from database.models.user import User
from database.repositories.proposed_change_repository import ApprovalRepository, ProposedChangeRepository
from infrastructure.checkpointer import get_checkpointer
from workflows.collector_workflow.dependencies import default_ai_provider, default_storage_adapter
from workflows.collector_workflow.graph import build_graph


class ReviewOutcome:
    """Result of resuming a paused review — surfaces whether Publish
    actually completed (only true for approve/edit_then_approve, and only
    once the graph has actually finished running past Publish) so the API
    layer can report an accurate status rather than assuming success."""

    def __init__(self, *, proposed_change: ProposedChange, published_restaurant_id: str | None, errors: list[dict]) -> None:
        self.proposed_change = proposed_change
        self.published_restaurant_id = published_restaurant_id
        self.errors = errors


class ReviewService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._proposed_changes = ProposedChangeRepository(session)
        self._approvals = ApprovalRepository(session)
        self._audit = AuditService(session)

    async def list_pending(self, *, limit: int = 100) -> list[ProposedChange]:
        return await self._proposed_changes.list_pending(limit=limit)

    async def get_detail(self, proposed_change_id: uuid.UUID) -> ProposedChange:
        record = await self._proposed_changes.get_by_id(proposed_change_id)
        if record is None:
            raise NotFoundError(f"No ProposedChange with id {proposed_change_id}")
        return record

    async def approve(self, proposed_change_id: uuid.UUID, *, reviewer: User, reason: str | None = None) -> ReviewOutcome:
        return await self._resume(
            proposed_change_id,
            action="approve",
            reviewer=reviewer,
            reason=reason,
            audit_action=AuditAction.PROPOSED_CHANGE_APPROVE,
        )

    async def reject(self, proposed_change_id: uuid.UUID, *, reviewer: User, reason: str | None = None) -> ReviewOutcome:
        return await self._resume(
            proposed_change_id,
            action="reject",
            reviewer=reviewer,
            reason=reason,
            audit_action=AuditAction.PROPOSED_CHANGE_REJECT,
        )

    async def edit_then_approve(
        self,
        proposed_change_id: uuid.UUID,
        *,
        reviewer: User,
        edited_structured_json: dict,
        reason: str | None = None,
    ) -> ReviewOutcome:
        record = await self._require_pending(proposed_change_id)

        # The edit itself is audited separately from the approval
        # decision — two distinct facts ("a human changed this data" and
        # "a human approved it") that a reviewer of the audit trail
        # shouldn't have to infer from one combined entry.
        await self._audit.log(
            action=AuditAction.PROPOSED_CHANGE_EDIT,
            entity_type=AuditEntityType.PROPOSED_CHANGE,
            entity_id=str(record.id),
            actor=reviewer,
            old_values={"structured_json": record.structured_json},
            new_values={"structured_json": edited_structured_json},
        )

        return await self._resume(
            proposed_change_id,
            action="edit_then_approve",
            reviewer=reviewer,
            reason=reason,
            audit_action=AuditAction.PROPOSED_CHANGE_APPROVE,
            edited_structured_json=edited_structured_json,
        )

    async def _require_pending(self, proposed_change_id: uuid.UUID) -> ProposedChange:
        record = await self._proposed_changes.get_by_id(proposed_change_id)
        if record is None:
            raise NotFoundError(f"No ProposedChange with id {proposed_change_id}")
        if record.status != ProposedChangeStatus.PENDING:
            raise ConflictError(
                f"ProposedChange {proposed_change_id} is {record.status.value}, not pending"
            )
        return record

    async def _resume(
        self,
        proposed_change_id: uuid.UUID,
        *,
        action: str,
        reviewer: User,
        reason: str | None,
        audit_action: AuditAction,
        edited_structured_json: dict | None = None,
    ) -> ReviewOutcome:
        record = await self._require_pending(proposed_change_id)
        if not record.thread_id:
            raise ConflictError(
                f"ProposedChange {proposed_change_id} has no thread_id — it wasn't created by "
                "a live collector run and cannot be resumed"
            )

        decision: dict[str, Any] = {"action": action, "proposed_change_id": str(record.id)}
        if edited_structured_json is not None:
            decision["edited_structured_json"] = edited_structured_json

        decision_status = {
            "approve": ProposedChangeStatus.APPROVED,
            "edit_then_approve": ProposedChangeStatus.APPROVED,
            "reject": ProposedChangeStatus.REJECTED,
        }[action]

        await self._approvals.create(
            proposed_change_id=record.id, decision=decision_status, reviewer_user_id=reviewer.id, reason=reason
        )
        await self._audit.log(
            action=audit_action,
            entity_type=AuditEntityType.PROPOSED_CHANGE,
            entity_id=str(record.id),
            actor=reviewer,
            metadata={"reason": reason},
        )

        errors: list[dict] = []
        published_restaurant_id: str | None = None

        async with get_checkpointer(self._settings) as checkpointer:
            graph = build_graph(
                self._session,
                storage=default_storage_adapter(self._settings),
                ai_provider=default_ai_provider(self._settings),
                checkpointer=checkpointer,
            )
            config = {"configurable": {"thread_id": record.thread_id}}
            result = await graph.ainvoke(Command(resume=decision), config)
            errors = result.get("errors", [])
            published_restaurant_id = result.get("published_restaurant_id")

        # Regardless of what the graph itself did, the ProposedChange row
        # is the source of truth an API caller/reviewer UI reads —
        # publish_node updates it to PUBLISHED on a real production
        # write; for reject/an approve that the graph didn't manage to
        # publish (an error downstream), reflect that status here so it
        # never reads PENDING once a decision has actually been made.
        final_status = ProposedChangeStatus.PUBLISHED if published_restaurant_id else decision_status
        await self._proposed_changes.update_status(record.id, status=final_status)
        await self._session.refresh(record)

        return ReviewOutcome(
            proposed_change=record, published_restaurant_id=published_restaurant_id, errors=errors
        )
