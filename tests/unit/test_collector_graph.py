"""Unit tests for the collector LangGraph topology: verifies the graph
compiles, has the expected node structure, and — using a real DB session
(Source Authority now genuinely persists an AgentRun row per run) with the
null entity-resolution provider — runs end-to-end through every node
without raising.

Source Authority's own behavior (verified/rejected/needs-review paths,
AgentRun/Source persistence, audit logging, never-hallucinate guarantee)
is covered in tests/unit/test_source_authority_node.py; this file only
covers the graph's shape and control flow."""

import pytest

from core.schemas.proposed_change import ProposedChangeStatus
from infrastructure.source_authority.null_provider import NullEntityResolutionProvider
from workflows.collector_workflow.graph import (
    NODE_DETERMINISTIC_VALIDATION,
    NODE_EXTRACTION,
    NODE_HUMAN_REVIEW,
    NODE_MULTIMODAL_TRANSLATION,
    NODE_PUBLISH,
    NODE_SOURCE_AUTHORITY,
    build_graph,
)


class TestGraphCompiles:
    def test_build_graph_returns_a_compiled_graph(self, db_session) -> None:
        graph = build_graph(db_session)
        assert graph is not None

    def test_build_graph_defaults_to_null_provider(self, db_session) -> None:
        # No explicit provider given — must not raise, and must not
        # require a real external API to be configured.
        graph = build_graph(db_session)
        assert graph is not None

    def test_build_graph_accepts_an_explicit_provider(self, db_session) -> None:
        graph = build_graph(db_session, NullEntityResolutionProvider())
        assert graph is not None

    def test_returns_a_fresh_instance_each_call(self, db_session) -> None:
        first = build_graph(db_session)
        second = build_graph(db_session)
        assert first is not second


class TestGraphTopology:
    def test_contains_all_six_required_nodes(self, db_session) -> None:
        graph = build_graph(db_session)
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

    def test_linear_edges_up_to_human_review(self, db_session) -> None:
        graph = build_graph(db_session)
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        assert (NODE_SOURCE_AUTHORITY, NODE_EXTRACTION) in edges
        assert (NODE_EXTRACTION, NODE_MULTIMODAL_TRANSLATION) in edges
        assert (NODE_MULTIMODAL_TRANSLATION, NODE_DETERMINISTIC_VALIDATION) in edges
        assert (NODE_DETERMINISTIC_VALIDATION, NODE_HUMAN_REVIEW) in edges

    def test_human_review_has_conditional_routes_to_publish_and_end(self, db_session) -> None:
        graph = build_graph(db_session)
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        assert (NODE_HUMAN_REVIEW, NODE_PUBLISH) in edges
        assert (NODE_HUMAN_REVIEW, "__end__") in edges

    def test_publish_leads_to_end(self, db_session) -> None:
        graph = build_graph(db_session)
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        assert (NODE_PUBLISH, "__end__") in edges


@pytest.mark.asyncio
class TestGraphExecutesEndToEnd:
    async def test_run_reaches_end_without_raising(self, db_session) -> None:
        graph = build_graph(db_session)
        result = await graph.ainvoke({"restaurant": None})
        assert "agent_run_id" not in result or result.get("agent_run_id") is None or isinstance(
            result.get("agent_run_id"), str
        )

    async def test_missing_restaurant_reports_a_source_authority_error(self, db_session) -> None:
        # No `restaurant` in the initial state at all — source_authority
        # must fail closed (an error, no fabricated source_url) rather
        # than raise an unhandled exception that would crash the run.
        graph = build_graph(db_session)
        result = await graph.ainvoke({})
        reporting_nodes = {error["node"] for error in result["errors"]}
        assert NODE_SOURCE_AUTHORITY in reporting_nodes
        assert "source_url" not in result

    async def test_downstream_placeholder_nodes_still_report_errors(self, db_session) -> None:
        graph = build_graph(db_session)
        result = await graph.ainvoke({})
        reporting_nodes = {error["node"] for error in result["errors"]}
        assert reporting_nodes == {
            NODE_SOURCE_AUTHORITY,
            NODE_EXTRACTION,
            NODE_MULTIMODAL_TRANSLATION,
            NODE_DETERMINISTIC_VALIDATION,
            NODE_HUMAN_REVIEW,
        }

    async def test_publish_does_not_run_when_approval_status_is_unset(self, db_session) -> None:
        graph = build_graph(db_session)
        result = await graph.ainvoke({})
        publish_ran = any(error["node"] == NODE_PUBLISH for error in result["errors"])
        assert not publish_ran

    async def test_explicit_approval_status_would_route_to_publish(self) -> None:
        from workflows.collector_workflow.graph import _route_after_human_review

        approved_state = {"human_approval_status": ProposedChangeStatus.APPROVED}
        assert _route_after_human_review(approved_state) == "publish"

        pending_state = {"human_approval_status": ProposedChangeStatus.PENDING}
        assert _route_after_human_review(pending_state) != "publish"
