"""Unit tests for the collector LangGraph skeleton: verifies the graph
compiles, has the expected node topology, and runs end-to-end through
every placeholder node without raising."""

import pytest

from core.schemas.proposed_change import ProposedChangeStatus
from workflows.collector_workflow.graph import (
    NODE_DETERMINISTIC_VALIDATION,
    NODE_EXTRACTION,
    NODE_HUMAN_REVIEW,
    NODE_MULTIMODAL_TRANSLATION,
    NODE_PUBLISH,
    NODE_SOURCE_AUTHORITY,
    build_graph,
    collector_graph,
)

class TestGraphCompiles:
    def test_module_level_graph_is_already_compiled(self) -> None:
        assert collector_graph is not None

    def test_build_graph_returns_a_fresh_compiled_graph(self) -> None:
        graph = build_graph()
        assert graph is not None
        assert graph is not collector_graph  # separate instance, not a shared singleton


class TestGraphTopology:
    def test_contains_all_six_required_nodes(self) -> None:
        nodes = set(collector_graph.get_graph().nodes.keys())
        expected = {
            NODE_SOURCE_AUTHORITY,
            NODE_EXTRACTION,
            NODE_MULTIMODAL_TRANSLATION,
            NODE_DETERMINISTIC_VALIDATION,
            NODE_HUMAN_REVIEW,
            NODE_PUBLISH,
        }
        assert expected.issubset(nodes)

    def test_linear_edges_up_to_human_review(self) -> None:
        edges = {(edge.source, edge.target) for edge in collector_graph.get_graph().edges}
        assert (NODE_SOURCE_AUTHORITY, NODE_EXTRACTION) in edges
        assert (NODE_EXTRACTION, NODE_MULTIMODAL_TRANSLATION) in edges
        assert (NODE_MULTIMODAL_TRANSLATION, NODE_DETERMINISTIC_VALIDATION) in edges
        assert (NODE_DETERMINISTIC_VALIDATION, NODE_HUMAN_REVIEW) in edges

    def test_human_review_has_conditional_routes_to_publish_and_end(self) -> None:
        edges = {(edge.source, edge.target) for edge in collector_graph.get_graph().edges}
        assert (NODE_HUMAN_REVIEW, NODE_PUBLISH) in edges
        assert (NODE_HUMAN_REVIEW, "__end__") in edges

    def test_publish_leads_to_end(self) -> None:
        edges = {(edge.source, edge.target) for edge in collector_graph.get_graph().edges}
        assert (NODE_PUBLISH, "__end__") in edges


@pytest.mark.asyncio
class TestGraphExecutesEndToEnd:
    async def test_run_reaches_end_without_raising(self) -> None:
        result = await collector_graph.ainvoke({"agent_run_id": "test-run"})
        assert result["agent_run_id"] == "test-run"

    async def test_every_placeholder_node_reports_an_error(self) -> None:
        result = await collector_graph.ainvoke({"agent_run_id": "test-run"})
        reporting_nodes = {error["node"] for error in result["errors"]}
        assert reporting_nodes == {
            NODE_SOURCE_AUTHORITY,
            NODE_EXTRACTION,
            NODE_MULTIMODAL_TRANSLATION,
            NODE_DETERMINISTIC_VALIDATION,
            NODE_HUMAN_REVIEW,
        }

    async def test_publish_does_not_run_when_approval_status_is_unset(self) -> None:
        # human_review_node is a placeholder and never sets
        # human_approval_status, so the conditional edge must route to END,
        # not Publish — an unset status must never be treated as approval.
        result = await collector_graph.ainvoke({"agent_run_id": "test-run"})
        publish_ran = any(error["node"] == NODE_PUBLISH for error in result["errors"])
        assert not publish_ran

    async def test_errors_accumulate_across_nodes_rather_than_overwrite(self) -> None:
        result = await collector_graph.ainvoke({"agent_run_id": "test-run"})
        assert len(result["errors"]) == 5

    async def test_explicit_approval_status_would_route_to_publish(self) -> None:
        # Directly exercises the routing function's branch for when a real
        # human_review_node eventually sets this — proves the conditional
        # edge itself (not just the placeholder body) is wired correctly.
        from workflows.collector_workflow.graph import _route_after_human_review

        approved_state = {"human_approval_status": ProposedChangeStatus.APPROVED}
        assert _route_after_human_review(approved_state) == NODE_PUBLISH

        pending_state = {"human_approval_status": ProposedChangeStatus.PENDING}
        assert _route_after_human_review(pending_state) != NODE_PUBLISH
