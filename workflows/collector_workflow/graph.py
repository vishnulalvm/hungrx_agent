"""Collector workflow graph.

Linear pipeline: Source Authority -> Extraction -> Multimodal Translation
-> Deterministic Validation -> Human Review -> Publish. Every node is now
fully wired to its real service; Human Review (Agent 5) is where the
graph actually pauses via LangGraph's `interrupt()` (see
workflows/collector_workflow/nodes/human_review.py) until an admin API
endpoint resumes it with an approve/reject/edit decision — see
apps/api/app/services/review_service.py for the resume side.

Human Review's conditional edge is what enforces that an unapproved
decision (REJECTED, or the run ending at the interrupt without ever
resuming) never reaches Publish: only `human_approval_status ==
ProposedChangeStatus.APPROVED` — which `human_review_node` only ever sets
from a real resumed decision, never a default — routes to Publish. Every
other outcome ends the run with nothing written to the production
restaurant/menu/dish tables.

Because Source Authority needs a live DB session/EntityResolutionProvider,
Extraction needs a live DB session/StorageAdapter/Settings, Multimodal
Translation needs a live DB session/StorageAdapter/AIProvider, and the
graph as a whole needs a checkpointer to durably pause/resume across
requests (see infrastructure/checkpointer.py), `build_graph` requires all
of them — a graph is scoped to one run's dependencies plus its
persistence backend, not a process-wide singleton.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.settings import Settings, get_settings
from infrastructure.ai.provider import AIProvider
from infrastructure.source_authority.null_provider import NullEntityResolutionProvider
from infrastructure.source_authority.provider import EntityResolutionProvider
from infrastructure.storage.base import StorageAdapter
from workflows.collector_workflow.nodes import (
    build_deterministic_validation_node,
    build_extraction_node,
    build_human_review_node,
    build_multimodal_translation_node,
    build_publish_node,
    build_source_authority_node,
)
from workflows.collector_workflow.state import CollectorState

NODE_SOURCE_AUTHORITY = "source_authority"
NODE_EXTRACTION = "extraction"
NODE_MULTIMODAL_TRANSLATION = "multimodal_translation"
NODE_DETERMINISTIC_VALIDATION = "deterministic_validation"
NODE_HUMAN_REVIEW = "human_review"
NODE_PUBLISH = "publish"


def _route_after_human_review(state: CollectorState) -> str:
    """Only an explicit APPROVED decision (see nodes/human_review.py —
    set exclusively from a resumed admin decision) routes to Publish.
    REJECTED, an unset status (the interrupted-but-not-yet-resumed case,
    since human_review_node's own return doesn't execute until resume),
    and any other value all end the run instead — never an implicit
    approval."""
    from core.schemas.proposed_change import ProposedChangeStatus

    if state.get("human_approval_status") == ProposedChangeStatus.APPROVED:
        return NODE_PUBLISH
    return END


def build_graph(
    session: AsyncSession,
    provider: EntityResolutionProvider | None = None,
    *,
    storage: StorageAdapter,
    ai_provider: AIProvider,
    checkpointer: BaseCheckpointSaver,
    settings: Settings | None = None,
) -> CompiledStateGraph:
    """`provider` defaults to NullEntityResolutionProvider (always
    NOT_FOUND, never a false positive) so a caller that hasn't wired up a
    real entity-resolution backend yet still gets a graph that compiles
    and runs safely rather than one that requires a provider to exist.
    `storage`, `ai_provider`, and `checkpointer` have no safe default —
    there's no "always fails closed" storage backend, "null" AI provider,
    or in-memory-only checkpointer that would be safe to silently fall
    back to for a workflow whose whole point is durably pausing across
    requests — so all three are required explicitly."""
    resolved_provider = provider if provider is not None else NullEntityResolutionProvider()
    resolved_settings = settings if settings is not None else get_settings()

    graph = StateGraph(CollectorState)

    graph.add_node(NODE_SOURCE_AUTHORITY, build_source_authority_node(session, resolved_provider))
    graph.add_node(
        NODE_EXTRACTION, build_extraction_node(session, storage, resolved_settings)
    )
    graph.add_node(
        NODE_MULTIMODAL_TRANSLATION, build_multimodal_translation_node(session, storage, ai_provider)
    )
    graph.add_node(NODE_DETERMINISTIC_VALIDATION, build_deterministic_validation_node(session))
    graph.add_node(NODE_HUMAN_REVIEW, build_human_review_node(session))
    graph.add_node(NODE_PUBLISH, build_publish_node(session))

    graph.add_edge(START, NODE_SOURCE_AUTHORITY)
    graph.add_edge(NODE_SOURCE_AUTHORITY, NODE_EXTRACTION)
    graph.add_edge(NODE_EXTRACTION, NODE_MULTIMODAL_TRANSLATION)
    graph.add_edge(NODE_MULTIMODAL_TRANSLATION, NODE_DETERMINISTIC_VALIDATION)
    graph.add_edge(NODE_DETERMINISTIC_VALIDATION, NODE_HUMAN_REVIEW)
    graph.add_conditional_edges(
        NODE_HUMAN_REVIEW, _route_after_human_review, {NODE_PUBLISH: NODE_PUBLISH, END: END}
    )
    graph.add_edge(NODE_PUBLISH, END)

    return graph.compile(checkpointer=checkpointer)
