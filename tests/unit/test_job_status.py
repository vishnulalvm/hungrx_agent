"""Unit tests for infrastructure.queue.job_status.get_job_status.

`_fixture_job_returning_agent_run_id` is a module-level function (not a
closure/lambda) since RQ workers resolve a job's callable by
import-path — it needs to be importable as
tests.unit.test_job_status._fixture_job_returning_agent_run_id.
"""

import pytest
import redis
from rq import Queue
from rq.worker import SimpleWorker

from infrastructure.queue.job_status import get_job_status


def _fixture_job_returning_agent_run_id(**kwargs):
    return {"agent_run_id": "run-123"}


@pytest.fixture
def redis_conn():
    conn = redis.Redis.from_url("redis://redis:6379/15")
    yield conn
    conn.flushdb()


class TestGetJobStatus:
    def test_unknown_job_id_returns_none(self, redis_conn) -> None:
        assert get_job_status("does-not-exist") is None

    def test_queued_job_reports_queued_status(self, redis_conn, monkeypatch) -> None:
        monkeypatch.setattr("infrastructure.queue.job_status.get_redis_connection", lambda: redis_conn)
        queue = Queue("test_queue", connection=redis_conn)
        job = queue.enqueue(_fixture_job_returning_agent_run_id)

        status = get_job_status(job.id)

        assert status is not None
        assert status["status"] == "queued"
        assert status["agent_run_id"] is None

    def test_finished_job_surfaces_agent_run_id_from_return_value(self, redis_conn, monkeypatch) -> None:
        monkeypatch.setattr("infrastructure.queue.job_status.get_redis_connection", lambda: redis_conn)
        queue = Queue("test_queue", connection=redis_conn)
        job = queue.enqueue(_fixture_job_returning_agent_run_id)
        SimpleWorker([queue], connection=redis_conn).work(burst=True)

        status = get_job_status(job.id)

        assert status["status"] == "finished"
        assert status["agent_run_id"] == "run-123"
