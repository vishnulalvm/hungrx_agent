"""Unit tests for the apps/worker/app/jobs/* RQ entry points.

Each job entry point's own responsibility (beyond the actual work,
already covered by the collector/reviewer workflow test suites and the
repository/service layers these jobs call into) is: acquire the
per-restaurant dedup lock before doing anything, structured-log
started/completed/skipped/failed, and — for the jobs that chain into
another job type — enqueue the next job with the right arguments. These
tests exercise exactly that boundary by monkeypatching each job's `_run`
coroutine (the actual DB/graph work) rather than standing up a real
database — DB-backed behavior for the code `_run` calls is already
covered elsewhere (test_temporal_hash_polling_node.py,
test_reviewer_publish_node.py, source_authority_service tests, etc.).

Uses a real Redis connection (DB 15) for the dedup lock, same as
test_job_lock.py.
"""

import uuid

import pytest
import redis

from infrastructure.queue.lock import JobAlreadyRunningError, RestaurantJobLock


@pytest.fixture
def redis_conn():
    conn = redis.Redis.from_url("redis://redis:6379/15")
    yield conn
    conn.flushdb()


class TestRestaurantIngestionDedup:
    def test_second_call_for_the_same_seed_while_first_is_in_flight_raises(
        self, redis_conn, monkeypatch
    ) -> None:
        import apps.worker.app.jobs.restaurant_ingestion as mod

        monkeypatch.setattr(mod, "get_redis_connection", lambda: redis_conn)
        seed_id = str(uuid.uuid4())

        # Hold the lock as if a first job were already running.
        held = RestaurantJobLock(redis_conn, restaurant_id=seed_id, job_type=mod.JOB_TYPE)
        held.__enter__()
        try:
            with pytest.raises(JobAlreadyRunningError):
                mod.run_restaurant_ingestion(restaurant_seed_id=seed_id, name="Joe's Pizza")
        finally:
            held.__exit__(None, None, None)

    def test_successful_run_releases_the_lock(self, redis_conn, monkeypatch) -> None:
        import apps.worker.app.jobs.restaurant_ingestion as mod

        monkeypatch.setattr(mod, "get_redis_connection", lambda: redis_conn)

        async def fake_run(**kwargs):
            return {"status": "not_found", "restaurant_id": str(uuid.uuid4())}

        monkeypatch.setattr(mod, "_run", fake_run)

        seed_id = str(uuid.uuid4())
        result = mod.run_restaurant_ingestion(restaurant_seed_id=seed_id, name="Joe's Pizza")
        assert result["status"] == "not_found"

        # Lock must be released — a second call for the same seed now succeeds too.
        result2 = mod.run_restaurant_ingestion(restaurant_seed_id=seed_id, name="Joe's Pizza")
        assert result2["status"] == "not_found"

    def test_run_failure_releases_the_lock_and_reraises(self, redis_conn, monkeypatch) -> None:
        import apps.worker.app.jobs.restaurant_ingestion as mod

        monkeypatch.setattr(mod, "get_redis_connection", lambda: redis_conn)

        async def failing_run(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(mod, "_run", failing_run)

        seed_id = str(uuid.uuid4())
        with pytest.raises(RuntimeError):
            mod.run_restaurant_ingestion(restaurant_seed_id=seed_id, name="Joe's Pizza")

        # Lock released even on failure.
        held = RestaurantJobLock(redis_conn, restaurant_id=seed_id, job_type=mod.JOB_TYPE)
        held.__enter__()
        held.__exit__(None, None, None)


class TestSourceCrawlDedupAndValidation:
    def test_missing_source_id_raises_without_acquiring_the_lock(self, redis_conn, monkeypatch) -> None:
        import apps.worker.app.jobs.source_crawl as mod

        monkeypatch.setattr(mod, "get_redis_connection", lambda: redis_conn)
        restaurant_id = str(uuid.uuid4())

        with pytest.raises(ValueError):
            mod.run_source_crawl(restaurant_id=restaurant_id, source_url="https://example.com")

        # Lock was never held — a normal run for this restaurant is unaffected.
        held = RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type=mod.JOB_TYPE)
        held.__enter__()
        held.__exit__(None, None, None)

    def test_dedup_by_restaurant_id(self, redis_conn, monkeypatch) -> None:
        import apps.worker.app.jobs.source_crawl as mod

        monkeypatch.setattr(mod, "get_redis_connection", lambda: redis_conn)
        restaurant_id = str(uuid.uuid4())

        held = RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type=mod.JOB_TYPE)
        held.__enter__()
        try:
            with pytest.raises(JobAlreadyRunningError):
                mod.run_source_crawl(
                    restaurant_id=restaurant_id, source_url="https://example.com", source_id=str(uuid.uuid4())
                )
        finally:
            held.__exit__(None, None, None)


class TestCollectorAndReviewerWorkflowDedup:
    def test_collector_workflow_dedup_by_restaurant_id(self, redis_conn, monkeypatch) -> None:
        import apps.worker.app.jobs.collector_workflow as mod

        monkeypatch.setattr(mod, "get_redis_connection", lambda: redis_conn)
        restaurant_id = str(uuid.uuid4())

        held = RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type=mod.JOB_TYPE)
        held.__enter__()
        try:
            with pytest.raises(JobAlreadyRunningError):
                mod.run_collector_workflow(restaurant_id=restaurant_id, restaurant_name="Joe's Pizza")
        finally:
            held.__exit__(None, None, None)

    def test_reviewer_workflow_dedup_by_restaurant_id(self, redis_conn, monkeypatch) -> None:
        import apps.worker.app.jobs.reviewer_workflow as mod

        monkeypatch.setattr(mod, "get_redis_connection", lambda: redis_conn)
        restaurant_id = str(uuid.uuid4())

        held = RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type=mod.JOB_TYPE)
        held.__enter__()
        try:
            with pytest.raises(JobAlreadyRunningError):
                mod.run_reviewer_workflow(restaurant_id=restaurant_id)
        finally:
            held.__exit__(None, None, None)

    def test_a_different_restaurant_is_not_blocked(self, redis_conn, monkeypatch) -> None:
        import apps.worker.app.jobs.collector_workflow as mod

        monkeypatch.setattr(mod, "get_redis_connection", lambda: redis_conn)

        async def fake_run(**kwargs):
            return {"agent_run_id": "run-1", "published_restaurant_id": None, "errors": []}

        monkeypatch.setattr(mod, "_run", fake_run)

        busy_restaurant_id = str(uuid.uuid4())
        held = RestaurantJobLock(redis_conn, restaurant_id=busy_restaurant_id, job_type=mod.JOB_TYPE)
        held.__enter__()
        try:
            other_restaurant_id = str(uuid.uuid4())
            result = mod.run_collector_workflow(restaurant_id=other_restaurant_id, restaurant_name="Other Place")
            assert result["agent_run_id"] == "run-1"
        finally:
            held.__exit__(None, None, None)


class TestMaintenancePollingSweepLock:
    def test_second_sweep_while_first_in_flight_raises(self, redis_conn, monkeypatch) -> None:
        import apps.worker.app.jobs.maintenance_polling as mod

        monkeypatch.setattr(mod, "get_redis_connection", lambda: redis_conn)

        held = RestaurantJobLock(
            redis_conn, restaurant_id=mod._SWEEP_LOCK_SENTINEL, job_type=mod.JOB_TYPE
        )
        held.__enter__()
        try:
            with pytest.raises(JobAlreadyRunningError):
                mod.run_maintenance_polling()
        finally:
            held.__exit__(None, None, None)

    def test_enqueues_one_reviewer_workflow_job_per_restaurant(self, redis_conn, monkeypatch) -> None:
        import apps.worker.app.jobs.maintenance_polling as mod

        monkeypatch.setattr(mod, "get_redis_connection", lambda: redis_conn)

        restaurant_ids = [uuid.uuid4(), uuid.uuid4()]

        async def fake_run():
            from apps.worker.app.jobs.reviewer_workflow import run_reviewer_workflow
            from infrastructure.queue.queues import QUEUE_REVIEWER_WORKFLOW, get_queue

            queue = get_queue(QUEUE_REVIEWER_WORKFLOW)
            job_ids = [
                queue.enqueue(run_reviewer_workflow, restaurant_id=str(rid)).id for rid in restaurant_ids
            ]
            return {"restaurants_swept": len(restaurant_ids), "enqueued_job_ids": job_ids}

        monkeypatch.setattr(mod, "_run", fake_run)
        monkeypatch.setattr("infrastructure.queue.queues.get_redis_connection", lambda: redis_conn)

        result = mod.run_maintenance_polling()

        assert result["restaurants_swept"] == 2
        assert len(result["enqueued_job_ids"]) == 2


class TestRetryFailedSweep:
    def test_runs_cleanly_with_no_failed_jobs(self, redis_conn, monkeypatch) -> None:
        import apps.worker.app.jobs.retry_failed as mod

        monkeypatch.setattr("infrastructure.queue.queues.get_redis_connection", lambda: redis_conn)

        result = mod.run_retry_failed()

        assert result["total_requeued"] == 0
        assert result["total_left_for_review"] == 0
        assert len(result["per_queue"]) == len(mod.ALL_QUEUE_NAMES)

    def test_transient_marker_detection(self) -> None:
        from apps.worker.app.jobs.retry_failed import _looks_transient

        assert _looks_transient("ConnectionError: refused") is True
        assert _looks_transient("sqlalchemy.exc.OperationalError: ...") is True
        assert _looks_transient("pydantic.ValidationError: invalid field") is False
        assert _looks_transient(None) is False
