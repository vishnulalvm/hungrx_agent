"""Collector workflow graph skeleton.

Linear pipeline: Source Authority -> Extraction -> Multimodal Translation
-> Deterministic Validation -> Human Review -> Publish. Source Authority
(Agent 1), Extraction (Agent 2), Multimodal Translation (Agent 3), and
Deterministic Validation (Agent 4) are now fully wired to their real
services; the remaining two nodes are still placeholders (see
workflows/collector_workflow/nodes/) — implementing them is out of scope
here.

Human Review is drawn as a conditional edge on purpose even though its
node body is a placeholder today: once interrupts land, an APPROVED
decision should route to Publish while REJECTED/PENDING should not — that
branching lives at the graph-topology level and won't need to change when
the node itself gains real logic.

Because Source Authority needs a live DB session/EntityResolutionProvider,
Extraction needs a live DB session/StorageAdapter/Settings, and
Multimodal Translation needs a live DB session/StorageAdapter/AIProvider,
`build_graph` requires all of them (settings defaults to the process-wide
`get_settings()` singleton, matching every other module; `ai_provider` has
no safe default — there's no "null" AI provider that produces meaningful
translation output, so a caller must supply one explicitly, same
reasoning as `storage`) — a graph is scoped to one run's dependencies, not
a process-wide singleton.
"""

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
    build_multimodal_translation_node,
    build_source_authority_node,
    human_review_node,
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
    session: AsyncSession,
    provider: EntityResolutionProvider | None = None,
    *,
    storage: StorageAdapter,
    ai_provider: AIProvider,
    settings: Settings | None = None,
) -> CompiledStateGraph:
    """`provider` defaults to NullEntityResolutionProvider (always
    NOT_FOUND, never a false positive) so a caller that hasn't wired up a
    real entity-resolution backend yet still gets a graph that compiles
    and runs safely rather than one that requires a provider to exist.
    `storage` and `ai_provider` have no safe default (unlike
    provider/settings, there's no "always fails closed" storage backend
    or "null" AI provider that produces meaningful output) so both are
    required explicitly — a caller must consciously choose where crawl
    captures land and which model backs translation."""
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
