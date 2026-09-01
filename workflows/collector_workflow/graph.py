"""Collector workflow graph skeleton.

Linear pipeline: Source Authority -> Extraction -> Multimodal Translation
-> Deterministic Validation -> Human Review -> Publish. Source Authority
(Agent 1) is now fully wired to SourceAuthorityService/AgentRunRepository/
AuditService; the remaining five nodes are still placeholders (see
workflows/collector_workflow/nodes/) — implementing them is out of scope
here.

Human Review is drawn as a conditional edge on purpose even though its
node body is a placeholder today: once interrupts land, an APPROVED
decision should route to Publish while REJECTED/PENDING should not — that
branching lives at the graph-topology level and won't need to change when
the node itself gains real logic.

Because Source Authority needs a live DB session and an
EntityResolutionProvider, `build_graph` now requires both — a graph is
scoped to one run's dependencies, not a process-wide singleton. The
module-level `collector_graph` below stays available for topology/import
smoke checks (draw_mermaid, node/edge listing) but should not be invoked
directly; a real run goes through `build_graph(session, provider)`.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.source_authority.null_provider import NullEntityResolutionProvider
from infrastructure.source_authority.provider import EntityResolutionProvider
from workflows.collector_workflow.nodes import (
    build_source_authority_node,
    deterministic_validation_node,
    extraction_node,
    human_review_node,
    multimodal_translation_node,
    publish_node,
)
from workflows.collector_workflow.state import CollectorState

NODE_SOURCE_AUTHORITY = "source_authority"
NODE_EXTRACTION = "extraction"
NODE_MULTIMODAL_TRANSLATION = "multimodal_translation"
NODE_DETERMINISTIC_VALIDATION = "deterministic_validation"
NODE_HUMAN_REVIEW = "human_review"
NODE_PUBLISH = "publish"


def _route_after_human_review(state: CollectorState) -> str:
    """Placeholder routing: until human_review_node actually sets
    `human_approval_status`, every run ends here rather than falling
    through to Publish — an unset/placeholder approval status must never
    be treated as an implicit approval."""
    from core.schemas.proposed_change import ProposedChangeStatus

    if state.get("human_approval_status") == ProposedChangeStatus.APPROVED:
        return NODE_PUBLISH
    return END


def build_graph(
    session: AsyncSession, provider: EntityResolutionProvider | None = None
) -> CompiledStateGraph:
    """`provider` defaults to NullEntityResolutionProvider (always
    NOT_FOUND, never a false positive) so a caller that hasn't wired up a
    real entity-resolution backend yet still gets a graph that compiles
    and runs safely rather than one that requires a provider to exist."""
    resolved_provider = provider if provider is not None else NullEntityResolutionProvider()

    graph = StateGraph(CollectorState)

    graph.add_node(NODE_SOURCE_AUTHORITY, build_source_authority_node(session, resolved_provider))
    graph.add_node(NODE_EXTRACTION, extraction_node)
    graph.add_node(NODE_MULTIMODAL_TRANSLATION, multimodal_translation_node)
    graph.add_node(NODE_DETERMINISTIC_VALIDATION, deterministic_validation_node)
    graph.add_node(NODE_HUMAN_REVIEW, human_review_node)
    graph.add_node(NODE_PUBLISH, publish_node)

    graph.add_edge(START, NODE_SOURCE_AUTHORITY)
    graph.add_edge(NODE_SOURCE_AUTHORITY, NODE_EXTRACTION)
    graph.add_edge(NODE_EXTRACTION, NODE_MULTIMODAL_TRANSLATION)
    graph.add_edge(NODE_MULTIMODAL_TRANSLATION, NODE_DETERMINISTIC_VALIDATION)
    graph.add_edge(NODE_DETERMINISTIC_VALIDATION, NODE_HUMAN_REVIEW)
    graph.add_conditional_edges(
        NODE_HUMAN_REVIEW, _route_after_human_review, {NODE_PUBLISH: NODE_PUBLISH, END: END}
    )
    graph.add_edge(NODE_PUBLISH, END)

    return graph.compile()
