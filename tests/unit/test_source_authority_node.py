"""Unit tests for the collector workflow's Source Authority node
(Agent 1) — run against a real Postgres transaction (see tests/conftest.py)
with fake EntityResolutionProvider implementations, so behavior is
exercised through the actual node function rather than mocked out."""

import uuid

import pytest
from sqlalchemy import select

from core.schemas.agent_run import AgentRunStatus, AgentWorkflowType
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.restaurant import Restaurant, RestaurantLocation
from core.schemas.source import SourceType
from core.schemas.source_authority import EntityCandidate, EntityResolutionQuery
from database.models.agent_run import AgentRun
from database.models.audit_log import AuditLog
from database.models.source import Source
from infrastructure.source_authority.null_provider import NullEntityResolutionProvider
from infrastructure.source_authority.provider import EntityResolutionProvider
from workflows.collector_workflow.nodes.source_authority import build_source_authority_node

pytestmark = pytest.mark.asyncio


class FakeProvider(EntityResolutionProvider):
    def __init__(self, candidates: list[EntityCandidate]) -> None:
        self._candidates = candidates
        self.received_queries: list[EntityResolutionQuery] = []

    async def resolve(self, query: EntityResolutionQuery) -> list[EntityCandidate]:
        self.received_queries.append(query)
        return self._candidates


def _restaurant(**overrides) -> Restaurant:
    payload = {
        "name": "Joe's Pizza",
        "locations": [
            RestaurantLocation(
                address_line1="1 Main St", city="Springfield", state="IL", country="US", phone="555-1234"
            )
        ],
    }
    payload.update(overrides)
    return Restaurant(**payload)


class TestIdentifiesOfficialWebsite:
    async def test_high_confidence_candidate_populates_source_url_on_state(self, db_session) -> None:
        restaurant = _restaurant()
        provider = FakeProvider(
            [EntityCandidate(url="https://joes-pizza.com/", provider_confidence=0.95, provider_name="fake")]
        )
        node = build_source_authority_node(db_session, provider)

        update = await node({"restaurant": restaurant})

        assert update["source_url"] == "https://joes-pizza.com/"
        assert "errors" not in update

    async def test_query_sent_to_provider_is_built_from_restaurant_and_location(self, db_session) -> None:
        restaurant = _restaurant()
        provider = FakeProvider(
            [EntityCandidate(url="https://joes-pizza.com/", provider_confidence=0.95, provider_name="fake")]
        )
        node = build_source_authority_node(db_session, provider)

        await node({"restaurant": restaurant})

        assert len(provider.received_queries) == 1
        query = provider.received_queries[0]
        assert query.restaurant_id == restaurant.id
        assert query.name == "Joe's Pizza"
        assert query.city == "Springfield"
        assert query.state == "IL"
        assert query.country == "US"


class TestRejectsAggregators:
    async def test_aggregator_only_result_never_sets_source_url(self, db_session) -> None:
        restaurant = _restaurant()
        provider = FakeProvider(
            [EntityCandidate(url="https://www.yelp.com/biz/joes-pizza", provider_confidence=0.99, provider_name="fake")]
        )
        node = build_source_authority_node(db_session, provider)

        update = await node({"restaurant": restaurant})

        assert "source_url" not in update
        assert "source" not in update

    async def test_aggregator_only_result_reports_an_error(self, db_session) -> None:
        restaurant = _restaurant()
        provider = FakeProvider(
            [EntityCandidate(url="https://order.doordash.com/store/joes-pizza", provider_confidence=0.99, provider_name="fake")]
        )
        node = build_source_authority_node(db_session, provider)

        update = await node({"restaurant": restaurant})

        assert len(update["errors"]) == 1
        assert update["errors"][0]["node"] == "source_authority"


class TestPersistsVerifiedSource:
    async def test_source_row_is_persisted_for_high_confidence_result(self, db_session) -> None:
        restaurant = _restaurant()
        provider = FakeProvider(
            [EntityCandidate(url="https://joes-pizza.com/", provider_confidence=0.9, provider_name="fake")]
        )
        node = build_source_authority_node(db_session, provider)

        update = await node({"restaurant": restaurant})

        source = update["source"]
        assert source is not None
        assert source.restaurant_id == restaurant.id
        assert source.source_type == SourceType.RESTAURANT_WEBSITE
        assert source.is_verified_domain is True

        row = await db_session.execute(select(Source).where(Source.id == source.id))
        assert row.scalar_one() is not None

    async def test_no_source_row_persisted_when_not_found(self, db_session) -> None:
        restaurant = _restaurant()
        node = build_source_authority_node(db_session, NullEntityResolutionProvider())

        await node({"restaurant": restaurant})

        rows = await db_session.execute(select(Source).where(Source.restaurant_id == restaurant.id))
        assert rows.scalar_one_or_none() is None


class TestCreatesAgentRunRecord:
    async def test_agent_run_created_on_every_invocation(self, db_session) -> None:
        restaurant = _restaurant()
        node = build_source_authority_node(db_session, NullEntityResolutionProvider())

        update = await node({"restaurant": restaurant})

        run_id = uuid.UUID(update["agent_run_id"])
        row = await db_session.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = row.scalar_one()
        assert run.workflow_type == AgentWorkflowType.COLLECTOR
        assert run.restaurant_id == restaurant.id

    async def test_agent_run_marked_failed_on_not_found(self, db_session) -> None:
        restaurant = _restaurant()
        node = build_source_authority_node(db_session, NullEntityResolutionProvider())

        update = await node({"restaurant": restaurant})

        run_id = uuid.UUID(update["agent_run_id"])
        row = await db_session.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = row.scalar_one()
        assert run.status == AgentRunStatus.FAILED
        assert run.error_message is not None

    async def test_agent_run_left_running_on_success(self, db_session) -> None:
        # Source Authority is only the first stage of the pipeline — it
        # must not mark the overall run SUCCEEDED itself; that belongs to
        # whatever finalizes the whole collector run once every stage
        # completes.
        restaurant = _restaurant()
        provider = FakeProvider(
            [EntityCandidate(url="https://joes-pizza.com/", provider_confidence=0.9, provider_name="fake")]
        )
        node = build_source_authority_node(db_session, provider)

        update = await node({"restaurant": restaurant})

        run_id = uuid.UUID(update["agent_run_id"])
        row = await db_session.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = row.scalar_one()
        assert run.status == AgentRunStatus.RUNNING


class TestLogsFailures:
    async def test_not_found_writes_an_audit_row(self, db_session) -> None:
        restaurant = _restaurant()
        node = build_source_authority_node(db_session, NullEntityResolutionProvider())

        update = await node({"restaurant": restaurant})

        run_id = update["agent_run_id"]
        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.AGENT_RUN, AuditLog.entity_id == run_id
            )
        )
        entry = rows.scalar_one()
        assert entry.action == AuditAction.AGENT_RUN_TRIGGER
        assert entry.metadata_["status"] == "not_found"

    async def test_rejected_writes_an_audit_row_with_rejected_candidates(self, db_session) -> None:
        restaurant = _restaurant()
        provider = FakeProvider(
            [EntityCandidate(url="https://www.facebook.com/joespizza", provider_confidence=0.9, provider_name="fake")]
        )
        node = build_source_authority_node(db_session, provider)

        update = await node({"restaurant": restaurant})

        run_id = update["agent_run_id"]
        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.AGENT_RUN, AuditLog.entity_id == run_id
            )
        )
        entry = rows.scalar_one()
        assert entry.metadata_["status"] == "rejected"
        assert "https://www.facebook.com/joespizza" in entry.metadata_["rejected_candidates"]

    async def test_verified_result_writes_no_failure_audit_row(self, db_session) -> None:
        restaurant = _restaurant()
        provider = FakeProvider(
            [EntityCandidate(url="https://joes-pizza.com/", provider_confidence=0.9, provider_name="fake")]
        )
        node = build_source_authority_node(db_session, provider)

        update = await node({"restaurant": restaurant})

        run_id = update["agent_run_id"]
        rows = await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.AGENT_RUN, AuditLog.entity_id == run_id
            )
        )
        assert rows.scalar_one_or_none() is None


class TestNeverHallucinatesUrls:
    async def test_missing_restaurant_on_state_fails_closed(self, db_session) -> None:
        node = build_source_authority_node(db_session, NullEntityResolutionProvider())

        update = await node({})

        assert "source_url" not in update
        assert len(update["errors"]) == 1

    async def test_not_found_never_fabricates_a_url(self, db_session) -> None:
        restaurant = _restaurant()
        node = build_source_authority_node(db_session, NullEntityResolutionProvider())

        update = await node({"restaurant": restaurant})

        assert "source_url" not in update
        assert "source" not in update

    async def test_low_confidence_candidate_is_not_treated_as_verified(self, db_session) -> None:
        # NEEDS_REVIEW (below the auto-verify threshold) must not surface
        # on source_url either — only a VERIFIED, persisted Source does.
        restaurant = _restaurant()
        provider = FakeProvider(
            [EntityCandidate(url="https://joes-pizza.com/", provider_confidence=0.4, provider_name="fake")]
        )
        node = build_source_authority_node(db_session, provider)

        update = await node({"restaurant": restaurant})

        assert "source_url" not in update
        assert "source" not in update
        assert len(update["errors"]) == 1

    async def test_multiple_rejected_candidates_still_produce_no_url(self, db_session) -> None:
        restaurant = _restaurant()
        provider = FakeProvider(
            [
                EntityCandidate(url="https://www.yelp.com/biz/joes-pizza", provider_confidence=0.99, provider_name="fake"),
                EntityCandidate(url="https://www.facebook.com/joespizza", provider_confidence=0.9, provider_name="fake"),
            ]
        )
        node = build_source_authority_node(db_session, provider)

        update = await node({"restaurant": restaurant})

        assert "source_url" not in update
