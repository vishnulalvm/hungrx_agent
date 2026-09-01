"""Reviewer workflow graph.

Pipeline: Temporal Hash Polling -> [Targeted Re-Extraction -> JSON Delta
Generation -> Delta Validation -> Human Final Sync] -> Publish.

Temporal Hash Polling is the workflow's early-out gate: when the
freshly polled source hash matches the last recorded one
(`hash_changed == False`), the run ends immediately — nothing in
brackets above ever executes. There is no point re-extracting, diffing,
validating, or interrupting a human for review when the source hasn't
changed since the last check; see nodes/temporal_hash_polling.py's
docstring for the polling/persistence details.

Human Final Sync's conditional edge mirrors the collector workflow's
`_route_after_human_review` exactly: only `human_approval_status ==
ProposedChangeStatus.APPROVED` — set exclusively by a real resumed admin
decision (see nodes/human_final_sync.py) — routes to Publish. Every
other outcome (REJECTED, or the run ending at the interrupt without ever
resuming) ends the run with nothing written to production.

Same dependency-injection shape as workflows/collector_workflow/graph.py:
`build_graph` needs a live DB session, a StorageAdapter, an AIProvider,
Settings, and a checkpointer — none has a safe default, for the same
reasons documented there (this graph pauses via interrupt() too, so it
needs a durable checkpointer to survive across separate API requests).
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.settings import Settings, get_settings
from core.schemas.proposed_change import ProposedChangeStatus
from infrastructure.ai.provider import AIProvider
from infrastructure.storage.base import StorageAdapter
from workflows.reviewer_workflow.nodes import (
    build_delta_validation_node,
    build_human_final_sync_node,
    build_json_delta_generation_node,
    build_publish_node,
    build_targeted_reextraction_node,
    build_temporal_hash_polling_node,
)
from workflows.reviewer_workflow.state import ReviewerState

NODE_TEMPORAL_HASH_POLLING = "temporal_hash_polling"
NODE_TARGETED_REEXTRACTION = "targeted_reextraction"
NODE_JSON_DELTA_GENERATION = "json_delta_generation"
NODE_DELTA_VALIDATION = "delta_validation"
NODE_HUMAN_FINAL_SYNC = "human_final_sync"
NODE_PUBLISH = "publish"


def _route_after_hash_polling(state: ReviewerState) -> str:
    """The workflow's early-stop gate: only an explicit `hash_changed ==
    True` continues on to re-extraction. An unset/False value — either a
    genuinely unchanged source, or temporal_hash_polling having failed
    closed (which leaves hash_changed unset, see that node's error
    branch) — ends the run instead. Never an implicit "continue"."""
    if state.get("hash_changed") is True:
        return NODE_TARGETED_REEXTRACTION
    return END


def _route_after_human_final_sync(state: ReviewerState) -> str:
    """Identical guarantee to workflows/collector_workflow/graph.py's
    `_route_after_human_review` — only a real resumed APPROVED decision
    reaches Publish."""
    if state.get("human_approval_status") == ProposedChangeStatus.APPROVED:
        return NODE_PUBLISH
    return END


def build_graph(
    session: AsyncSession,
    *,
    storage: StorageAdapter,
    ai_provider: AIProvider,
    checkpointer: BaseCheckpointSaver,
    settings: Settings | None = None,
) -> CompiledStateGraph:
    resolved_settings = settings if settings is not None else get_settings()

    graph = StateGraph(ReviewerState)

    graph.add_node(
        NODE_TEMPORAL_HASH_POLLING, build_temporal_hash_polling_node(session, storage, resolved_settings)
    )
    graph.add_node(
        NODE_TARGETED_REEXTRACTION,
        build_targeted_reextraction_node(session, storage, resolved_settings, ai_provider),
    )
    graph.add_node(NODE_JSON_DELTA_GENERATION, build_json_delta_generation_node())
    graph.add_node(NODE_DELTA_VALIDATION, build_delta_validation_node(session))
    graph.add_node(NODE_HUMAN_FINAL_SYNC, build_human_final_sync_node(session))
    graph.add_node(NODE_PUBLISH, build_publish_node(session))

    graph.add_edge(START, NODE_TEMPORAL_HASH_POLLING)
    graph.add_conditional_edges(
        NODE_TEMPORAL_HASH_POLLING,
        _route_after_hash_polling,
        {NODE_TARGETED_REEXTRACTION: NODE_TARGETED_REEXTRACTION, END: END},
    )
    graph.add_edge(NODE_TARGETED_REEXTRACTION, NODE_JSON_DELTA_GENERATION)
    graph.add_edge(NODE_JSON_DELTA_GENERATION, NODE_DELTA_VALIDATION)
    graph.add_edge(NODE_DELTA_VALIDATION, NODE_HUMAN_FINAL_SYNC)
    graph.add_conditional_edges(
        NODE_HUMAN_FINAL_SYNC, _route_after_human_final_sync, {NODE_PUBLISH: NODE_PUBLISH, END: END}
    )
    graph.add_edge(NODE_PUBLISH, END)

    return graph.compile(checkpointer=checkpointer)
