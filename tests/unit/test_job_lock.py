"""Unit tests for infrastructure.queue.lock.RestaurantJobLock — the
Redis SETNX dedup lock jobs use to avoid two overlapping runs for the
same restaurant/job_type. Uses a real Redis connection (DB index 15,
separate from the dev/prod default DB 0) since there's no fakeredis in
this repo's dependencies and RQ/Redis wiring is worth testing against
the real thing, same rationale tests/conftest.py gives for using a real
Postgres rather than mocks.
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


class TestAcquireAndRelease:
    def test_acquires_and_releases_cleanly(self, redis_conn) -> None:
        restaurant_id = str(uuid.uuid4())
        with RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type="collector_workflow"):
            pass
        # Lock released on exit — a second acquire must succeed.
        with RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type="collector_workflow"):
            pass

    def test_released_even_on_exception_inside_the_block(self, redis_conn) -> None:
        restaurant_id = str(uuid.uuid4())
        with pytest.raises(ValueError):
            with RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type="collector_workflow"):
                raise ValueError("boom")

        with RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type="collector_workflow"):
            pass


class TestDedup:
    def test_second_concurrent_acquire_for_same_restaurant_and_job_type_raises(self, redis_conn) -> None:
        restaurant_id = str(uuid.uuid4())
        with RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type="collector_workflow"):
            with pytest.raises(JobAlreadyRunningError):
                with RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type="collector_workflow"):
                    pass

    def test_different_job_type_for_same_restaurant_does_not_conflict(self, redis_conn) -> None:
        restaurant_id = str(uuid.uuid4())
        with RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type="collector_workflow"):
            with RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type="reviewer_workflow"):
                pass

    def test_different_restaurant_for_same_job_type_does_not_conflict(self, redis_conn) -> None:
        with RestaurantJobLock(redis_conn, restaurant_id=str(uuid.uuid4()), job_type="collector_workflow"):
            with RestaurantJobLock(redis_conn, restaurant_id=str(uuid.uuid4()), job_type="collector_workflow"):
                pass


class TestStaleOwnerNeverReleasesANewerLock:
    def test_expired_then_reacquired_lock_is_not_released_by_the_stale_holder(self, redis_conn) -> None:
        restaurant_id = str(uuid.uuid4())
        stale_lock = RestaurantJobLock(
            redis_conn, restaurant_id=restaurant_id, job_type="collector_workflow", ttl_seconds=60
        )
        stale_lock.__enter__()

        # Simulate the lock expiring (crash-without-release) by deleting
        # the key directly, then a second job legitimately acquiring it.
        key = f"hungrx:job-lock:collector_workflow:{restaurant_id}"
        redis_conn.delete(key)
        newer_lock = RestaurantJobLock(redis_conn, restaurant_id=restaurant_id, job_type="collector_workflow")
        newer_lock.__enter__()

        # The stale holder's __exit__ must not delete the newer lock's key.
        stale_lock.__exit__(None, None, None)
        assert redis_conn.get(key) is not None

        newer_lock.__exit__(None, None, None)
        assert redis_conn.get(key) is None
