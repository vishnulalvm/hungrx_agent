"""Unit test for AgentRunRepository.list_paginated — added for the admin
dashboard's agent-runs page. Everything else on this repository is
already covered via the node tests that call create/mark_succeeded/
mark_failed/update_metrics.
"""

import pytest

from core.schemas.agent_run import AgentWorkflowType
from database.repositories.agent_run_repository import AgentRunRepository

pytestmark = pytest.mark.asyncio


class TestListPaginated:
    async def test_returns_every_run_ordered_by_created_at_desc(self, db_session) -> None:
        # created_at uses server_default=func.now(), which Postgres
        # resolves once per transaction (not once per statement) — two
        # inserts in the same test transaction can share an identical
        # timestamp, so this asserts the query is actually sorted
        # (non-increasing created_at) rather than a specific tie-break
        # order between rows created in the same instant.
        repo = AgentRunRepository(db_session)
        first = await repo.create(workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=None)
        second = await repo.create(workflow_type=AgentWorkflowType.REVIEWER, restaurant_id=None)

        runs, total = await repo.list_paginated(page=1, page_size=20)

        assert total == 2
        assert {run.id for run in runs} == {first.id, second.id}
        assert all(runs[i].created_at >= runs[i + 1].created_at for i in range(len(runs) - 1))

    async def test_pagination_window(self, db_session) -> None:
        repo = AgentRunRepository(db_session)
        for _ in range(3):
            await repo.create(workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=None)

        page_one, total = await repo.list_paginated(page=1, page_size=2)
        page_two, _ = await repo.list_paginated(page=2, page_size=2)

        assert total == 3
        assert len(page_one) == 2
        assert len(page_two) == 1

    async def test_empty_when_no_runs(self, db_session) -> None:
        runs, total = await AgentRunRepository(db_session).list_paginated(page=1, page_size=20)
        assert runs == []
        assert total == 0
