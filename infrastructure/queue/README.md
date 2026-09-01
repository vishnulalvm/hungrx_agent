# infrastructure/queue/

Redis-backed background job queue infrastructure, built on
[RQ](https://python-rq.org/) (already pinned in `pyproject.toml`; the
worker Dockerfile's own comment calls out "RQ over Redis"). Job code
itself lives in `apps/worker/app/jobs/` — this module is the shared
plumbing every job type uses.

## Modules

- `redis_connection.py` — `get_redis_connection()`: one process-wide
  `redis.Redis` sync connection (RQ has no native asyncio support),
  same `lru_cache`'d-singleton shape as `database/session.py`'s
  `get_engine()`.
- `queues.py` — one named `Queue` per job type
  (`restaurant_ingestion`, `source_crawl`, `collector_workflow`,
  `maintenance_polling`, `reviewer_workflow`) rather than one shared
  queue, so a worker fleet can be scaled/prioritized per job type later
  without changing job code. `DEFAULT_RETRY` (3 attempts total, 30s/120s
  backoff) is applied uniformly by every job's `queue.enqueue(...)`
  call. `failed_job_registry(queue_name)` exposes RQ's own
  `FailedJobRegistry` — the dead-letter store a job lands in once it
  exhausts `DEFAULT_RETRY`.
- `lock.py` — `RestaurantJobLock`: a Redis `SET NX PX` dedup lock keyed
  by `(restaurant_id, job_type)`. Every job in `apps/worker/app/jobs/`
  acquires this before doing any work and raises
  `JobAlreadyRunningError` (not a retry-worthy failure — the caller
  should treat "another job for this restaurant is already running" as
  "skip," not "try again") if it's already held. TTL-based, not
  heartbeat-extended — deliberately minimal, tolerates a stale lock
  expiring a little early rather than needing lock-renewal machinery.
- `job_status.py` — `get_job_status(job_id)`: RQ's own per-job status
  (queued/started/finished/failed) plus, for a finished workflow job,
  the `agent_run_id` its return value carries — the RQ job id and the
  LangGraph `AgentRun.id` a `collector_workflow`/`reviewer_workflow` job
  produces are different ids (the AgentRun doesn't exist until partway
  through the run), so this is the one place that correlates them.
- `async_bridge.py` — `run_async(coro_fn, *args, **kwargs)`: the one
  `asyncio.run(...)` wrapper every job module uses to call into the
  otherwise-async application code (SQLAlchemy `AsyncSession`, the
  LangGraph graphs, the checkpointer) from RQ's sync job-execution path.
- `redis.conf` — Redis persistence config (RDB + AOF) for the
  `redis` docker-compose service; not Python, just server config.

## Job types (implemented in `apps/worker/app/jobs/`)

| Job | Queue | Chains into |
|---|---|---|
| `restaurant_ingestion` | `restaurant_ingestion` | `source_crawl` (on a VERIFIED source) |
| `source_crawl` | `source_crawl` | `collector_workflow` |
| `collector_workflow` | `collector_workflow` | — (pauses at human_review) |
| `maintenance_polling` | `maintenance_polling` | `reviewer_workflow` (one per published restaurant) |
| `reviewer_workflow` | `reviewer_workflow` | — (pauses at human_final_sync, or early-stops if unchanged) |
| `retry_failed` | (sweeps every queue's `FailedJobRegistry`) | — |

See `apps/worker/README.md` for what each job actually does and its
idempotency/retry/audit-linkage guarantees.

## Retry policy

`DEFAULT_RETRY` (queues.py) gives every job 2 automatic retries (3
attempts total) with 30s/120s backoff before RQ moves it to that queue's
`FailedJobRegistry` — the dead-letter equivalent. `retry_failed`
(`apps/worker/app/jobs/retry_failed.py`) is a separate, schedulable job
that sweeps every queue's `FailedJobRegistry` and requeues only jobs
whose last failure looks transient (a connection/timeout/DB-operational
error, via `_looks_transient`'s exception-name matching) — anything else
(a validation error, a domain rejection) is left in place for a human to
look at, since re-running a job that will deterministically fail the
same way again wastes compute and hides a real bug behind "it'll retry
eventually."

## Testing

Real Redis (DB index 15 — separate from the dev/prod default DB 0, no
fakeredis dependency in this repo) via
`tests/unit/test_job_lock.py`, `tests/unit/test_job_status.py`, and
`tests/unit/test_worker_jobs.py`. Job-level tests monkeypatch each job's
own `_run` coroutine (the actual DB/graph work, already covered by the
collector/reviewer workflow test suites) so they can exercise the
locking/logging/enqueue-chaining boundary without needing
`TEST_DATABASE_URL` wired into the module-level `get_settings()`/
`get_sessionmaker()` singletons these jobs call directly (unlike
FastAPI endpoints, job entry points aren't behind a dependency-injection
seam a test can override).
