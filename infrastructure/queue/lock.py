"""Redis-backed dedup lock: prevents the same restaurant from having two
overlapping jobs of the same type in flight at once (e.g. two
`collector_workflow` runs for the same restaurant racing each other, or
a `maintenance_polling` tick firing again before the previous one for
that restaurant finished).

Implemented as a single `SET key value NX PX` — atomic, so two workers
racing to acquire the same key can never both succeed — with a TTL so a
worker that crashes/is killed without releasing the lock doesn't wedge
that restaurant's jobs forever; the lock self-heals once the TTL elapses.
Not a general-purpose distributed lock (no fencing tokens, no
lock-extension/heartbeat) — deliberately minimal for this one job-dedup
use case, which tolerates a stale lock expiring a little early.
"""

import uuid

import redis

_DEFAULT_TTL_SECONDS = 30 * 60  # generous upper bound on any one job's runtime


class JobAlreadyRunningError(Exception):
    """Raised when a lock for this (restaurant_id, job_type) is already
    held by another in-flight job."""

    def __init__(self, *, restaurant_id: str, job_type: str) -> None:
        self.restaurant_id = restaurant_id
        self.job_type = job_type
        super().__init__(f"A {job_type} job is already running for restaurant {restaurant_id}")


def _lock_key(*, restaurant_id: str, job_type: str) -> str:
    return f"hungrx:job-lock:{job_type}:{restaurant_id}"


class RestaurantJobLock:
    """Usage:

        with RestaurantJobLock(redis_conn, restaurant_id=..., job_type="collector_workflow"):
            ... do the work ...

    Raises JobAlreadyRunningError immediately (does not block/wait) if
    another job already holds this restaurant+job_type's lock — callers
    should treat that as "skip this job, one's already in flight," not
    as a failure to retry.
    """

    def __init__(
        self,
        connection: redis.Redis,
        *,
        restaurant_id: str,
        job_type: str,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._connection = connection
        self._restaurant_id = restaurant_id
        self._job_type = job_type
        self._ttl_seconds = ttl_seconds
        self._key = _lock_key(restaurant_id=restaurant_id, job_type=job_type)
        self._token = str(uuid.uuid4())

    def __enter__(self) -> "RestaurantJobLock":
        acquired = self._connection.set(self._key, self._token, nx=True, px=self._ttl_seconds * 1000)
        if not acquired:
            raise JobAlreadyRunningError(restaurant_id=self._restaurant_id, job_type=self._job_type)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        # Only release if we still hold it (token match) — a lock that
        # already expired and was re-acquired by a newer job must never
        # be released by this stale holder.
        _release_if_owned(self._connection, key=self._key, token=self._token)


_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _release_if_owned(connection: redis.Redis, *, key: str, token: str) -> None:
    connection.eval(_RELEASE_SCRIPT, 1, key, token)
