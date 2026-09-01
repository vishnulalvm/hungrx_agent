"""Collector workflow graph skeleton.

Linear pipeline for now: Source Authority -> Extraction -> Multimodal
Translation -> Deterministic Validation -> Human Review -> Publish. Every
node is currently a placeholder (see workflows/collector_workflow/nodes/)
except Source Authority, which has a real service behind it
(SourceAuthorityService) but isn't wired into the node yet either — this
task is scoped to the graph/state skeleton compiling end-to-end, not node
logic.

Human Review is drawn as a conditional edge on purpose even though its
node body is a placeholder today: once interrupts land, an APPROVED
decision should route to Publish while REJECTED/PENDING should not — that
branching lives at the graph-topology level and won't need to change when
the node itself gains real logic.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from workflows.collector_workflow.nodes import (
    deterministic_validation_node,
    extraction_node,
    human_review_node,
    multimodal_translation_node,
    publish_node,
    source_authority_node,
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


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(CollectorState)

    graph.add_node(NODE_SOURCE_AUTHORITY, source_authority_node)
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


collector_graph = build_graph()
