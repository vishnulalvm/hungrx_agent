"""Unit tests for the collector LangGraph topology: verifies the graph
compiles, has the expected node structure, and — using a real DB session
(Source Authority now genuinely persists an AgentRun row per run) with the
null entity-resolution provider — runs end-to-end through every node
without raising.

Source Authority's own behavior (verified/rejected/needs-review paths,
AgentRun/Source persistence, audit logging, never-hallucinate guarantee)
is covered in tests/unit/test_source_authority_node.py; Extraction's own
behavior (page discovery, snapshot persistence, HTML/PDF handling) is
covered in tests/unit/test_extraction_node.py; Human Review's actual
pause/resume behavior (the interrupt itself, idempotent record creation,
approve/reject/edit_then_approve) is covered in
tests/unit/test_human_review_node.py and
tests/integration/test_human_in_the_loop.py, which use the real
Postgres-backed checkpointer since that's the whole point of what they
test. This file only covers the graph's shape and control flow, so an
in-memory checkpointer (MemorySaver) is enough — nothing here actually
needs a durable pause."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from core.schemas.proposed_change import ProposedChangeStatus
from infrastructure.ai.provider import AIProvider
from infrastructure.source_authority.null_provider import NullEntityResolutionProvider
from infrastructure.storage.local_storage import LocalStorageAdapter
from workflows.collector_workflow.graph import (
    NODE_DETERMINISTIC_VALIDATION,
    NODE_EXTRACTION,
    NODE_HUMAN_REVIEW,
    NODE_MULTIMODAL_TRANSLATION,
    NODE_PUBLISH,
    NODE_SOURCE_AUTHORITY,
    build_graph,
)


@pytest.fixture
def storage(tmp_path):
    return LocalStorageAdapter(tmp_path)


class _UnusedAIProvider(AIProvider):
    """Every end-to-end run in this file starts with no `restaurant` on
    state, so source_authority fails closed before extraction or
    multimodal_translation ever run — this fake exists only to satisfy
    build_graph's required parameter and asserts it's never actually
    called."""

    async def generate_structured(self, *, system_prompt, user_content, response_model):
        raise AssertionError("AIProvider should not be called in graph-topology tests")


@pytest.fixture
def ai_provider():
    return _UnusedAIProvider()


@pytest.fixture
def checkpointer():
    return InMemorySaver()


def _build(db_session, storage, ai_provider, checkpointer, provider=None):
    return build_graph(
        db_session, provider, storage=storage, ai_provider=ai_provider, checkpointer=checkpointer
    )


class TestGraphCompiles:
    def test_build_graph_returns_a_compiled_graph(self, db_session, storage, ai_provider, checkpointer) -> None:
        graph = _build(db_session, storage, ai_provider, checkpointer)
        assert graph is not None

    def test_build_graph_defaults_to_null_provider(self, db_session, storage, ai_provider, checkpointer) -> None:
        # No explicit provider given — must not raise, and must not
        # require a real external API to be configured.
        graph = _build(db_session, storage, ai_provider, checkpointer)
        assert graph is not None

    def test_build_graph_accepts_an_explicit_provider(self, db_session, storage, ai_provider, checkpointer) -> None:
        graph = _build(db_session, storage, ai_provider, checkpointer, provider=NullEntityResolutionProvider())
        assert graph is not None

    def test_returns_a_fresh_instance_each_call(self, db_session, storage, ai_provider, checkpointer) -> None:
        first = _build(db_session, storage, ai_provider, checkpointer)
        second = _build(db_session, storage, ai_provider, checkpointer)
        assert first is not second

    def test_requires_a_checkpointer(self, db_session, storage, ai_provider) -> None:
        with pytest.raises(TypeError):
            build_graph(db_session, storage=storage, ai_provider=ai_provider)


class TestGraphTopology:
    def test_contains_all_six_required_nodes(self, db_session, storage, ai_provider, checkpointer) -> None:
        graph = _build(db_session, storage, ai_provider, checkpointer)
        nodes = set(graph.get_graph().nodes.keys())
        expected = {
            NODE_SOURCE_AUTHORITY,
            NODE_EXTRACTION,
            NODE_MULTIMODAL_TRANSLATION,
            NODE_DETERMINISTIC_VALIDATION,
            NODE_HUMAN_REVIEW,
            NODE_PUBLISH,
        }
        assert expected.issubset(nodes)

    def test_linear_edges_up_to_human_review(self, db_session, storage, ai_provider, checkpointer) -> None:
        graph = _build(db_session, storage, ai_provider, checkpointer)
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        assert (NODE_SOURCE_AUTHORITY, NODE_EXTRACTION) in edges
        assert (NODE_EXTRACTION, NODE_MULTIMODAL_TRANSLATION) in edges
        assert (NODE_MULTIMODAL_TRANSLATION, NODE_DETERMINISTIC_VALIDATION) in edges
        assert (NODE_DETERMINISTIC_VALIDATION, NODE_HUMAN_REVIEW) in edges

    def test_human_review_has_conditional_routes_to_publish_and_end(
        self, db_session, storage, ai_provider, checkpointer
    ) -> None:
        graph = _build(db_session, storage, ai_provider, checkpointer)
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        assert (NODE_HUMAN_REVIEW, NODE_PUBLISH) in edges
        assert (NODE_HUMAN_REVIEW, "__end__") in edges

    def test_publish_leads_to_end(self, db_session, storage, ai_provider, checkpointer) -> None:
        graph = _build(db_session, storage, ai_provider, checkpointer)
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        assert (NODE_PUBLISH, "__end__") in edges


@pytest.mark.asyncio
class TestGraphExecutesEndToEnd:
    """Every run here starts with no `restaurant` on state, so
    source_authority fails closed immediately — the run never reaches
    the human_review interrupt, so a plain ainvoke (no resume) is enough
    and every node still reports its own placeholder/fail-closed error."""

    async def test_run_reaches_end_without_raising(self, db_session, storage, ai_provider, checkpointer) -> None:
        graph = _build(db_session, storage, ai_provider, checkpointer)
        config = {"configurable": {"thread_id": "topology-test-1"}}
        result = await graph.ainvoke({"restaurant": None}, config)
        assert "agent_run_id" not in result or result.get("agent_run_id") is None or isinstance(
            result.get("agent_run_id"), str
        )

    async def test_missing_restaurant_reports_a_source_authority_error(
        self, db_session, storage, ai_provider, checkpointer
    ) -> None:
        # No `restaurant` in the initial state at all — source_authority
        # must fail closed (an error, no fabricated source_url) rather
        # than raise an unhandled exception that would crash the run.
        graph = _build(db_session, storage, ai_provider, checkpointer)
        config = {"configurable": {"thread_id": "topology-test-2"}}
        result = await graph.ainvoke({}, config)
        reporting_nodes = {error["node"] for error in result["errors"]}
        assert NODE_SOURCE_AUTHORITY in reporting_nodes
        assert "source_url" not in result

    async def test_downstream_placeholder_nodes_still_report_errors(
        self, db_session, storage, ai_provider, checkpointer
    ) -> None:
        # source_authority fails closed (no restaurant on state), so
        # every downstream node also fails closed on missing input,
        # human_review included (it never reaches its interrupt() call
        # since structured_json/agent_run_id are never populated).
        graph = _build(db_session, storage, ai_provider, checkpointer)
        config = {"configurable": {"thread_id": "topology-test-3"}}
        result = await graph.ainvoke({}, config)
        reporting_nodes = {error["node"] for error in result["errors"]}
        assert reporting_nodes == {
            NODE_SOURCE_AUTHORITY,
            NODE_EXTRACTION,
            NODE_MULTIMODAL_TRANSLATION,
            NODE_DETERMINISTIC_VALIDATION,
            NODE_HUMAN_REVIEW,
        }

    async def test_publish_does_not_run_when_approval_status_is_unset(
        self, db_session, storage, ai_provider, checkpointer
    ) -> None:
        graph = _build(db_session, storage, ai_provider, checkpointer)
        config = {"configurable": {"thread_id": "topology-test-4"}}
        result = await graph.ainvoke({}, config)
        publish_ran = any(error["node"] == NODE_PUBLISH for error in result["errors"])
        assert not publish_ran

    async def test_explicit_approval_status_would_route_to_publish(self) -> None:
        from workflows.collector_workflow.graph import _route_after_human_review

        approved_state = {"human_approval_status": ProposedChangeStatus.APPROVED}
        assert _route_after_human_review(approved_state) == "publish"

        pending_state = {"human_approval_status": ProposedChangeStatus.PENDING}
        assert _route_after_human_review(pending_state) != "publish"
