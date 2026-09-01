# apps/worker/

Background job worker, Redis-backed via RQ (see
`infrastructure/queue/README.md` for the queue/lock/retry plumbing).
`app/main.py` starts one RQ `Worker` listening on every named queue.

## Job types (`app/jobs/`)

- `restaurant_ingestion.py` — onboards a brand-new restaurant: no
  restaurant row exists yet (the production `restaurants` table is only
  ever written by the collector workflow's publish node), so this job
  runs Source Authority resolution (`SourceAuthorityService`) against a
  name/location seed. On `VERIFIED`, enqueues `source_crawl`. On
  `NOT_FOUND`/`REJECTED`/`NEEDS_REVIEW`, the run ends here — nothing to
  crawl without a verified domain. Idempotency key: `restaurant_seed_id`
  (a caller-supplied stable id for "this ingestion request" — no
  restaurant id exists yet to dedup on).
- `source_crawl.py` — fetches a restaurant's verified source URL
  (`infrastructure/crawler/CrawlerService`), persists the resulting
  `SourceSnapshot` (durable — gives the reviewer workflow's Temporal
  Hash Polling a baseline hash to compare against later), then enqueues
  `collector_workflow`. Idempotency key: `restaurant_id`.
- `collector_workflow.py` — runs
  `workflows.collector_workflow.graph.build_graph` from `START` with
  just a `Restaurant` identity on state. The graph's own
  `source_authority` node re-resolves/re-verifies the site and creates
  its own `AgentRun`; `extraction` independently captures the
  menu/nutrition pages it needs (a deeper, multi-page capture than
  `source_crawl`'s single root-page fetch — the two don't duplicate each
  other's work). Returns once the run pauses at `human_review`'s
  `interrupt()` or finishes — never blocks on an admin decision.
  Idempotency key: `restaurant_id`.
- `maintenance_polling.py` — the periodic sweep CLAUDE.md's Status
  section used to call out as "not yet built": enumerates every
  published restaurant (`RestaurantRepository.list_ids`) and enqueues
  one `reviewer_workflow` job per restaurant. Intended to be triggered
  on a schedule (RQ's `Worker.work(with_scheduler=True)`, already
  enabled in `main.py`, or an external cron hitting this job) — does no
  hashing/fetching itself, so a sweep never blocks on N restaurants'
  worth of network I/O.
- `reviewer_workflow.py` — runs
  `workflows.reviewer_workflow.graph.build_graph` for one already-
  published restaurant. Early-stops inside the graph itself
  (`temporal_hash_polling`'s unchanged-hash gate) when nothing's
  changed; otherwise carries through to `human_final_sync`'s
  `interrupt()`. Idempotency key: `restaurant_id`.
- `retry_failed.py` — sweeps every queue's `FailedJobRegistry` (RQ's
  dead-letter store) and requeues jobs whose failure looks transient;
  see `infrastructure/queue/README.md`'s Retry policy section.

## Cross-cutting guarantees

- **Idempotency / no duplicate work**: every job (except
  `restaurant_ingestion`, keyed on a caller-supplied seed id, and
  `maintenance_polling`'s sweep itself, keyed on a fixed sentinel)
  acquires `infrastructure.queue.lock.RestaurantJobLock` keyed on
  `restaurant_id` before doing any work, and raises
  `JobAlreadyRunningError` (propagated, not swallowed — the caller sees
  a clean "skip" rather than a duplicate run) if another job for that
  restaurant/job-type is already in flight.
- **Retry policy**: `infrastructure/queue/queues.py`'s `DEFAULT_RETRY` —
  every `queue.enqueue(...)` call across all job types gets 2 automatic
  retries (30s/120s backoff) before landing in that queue's
  `FailedJobRegistry`.
- **Dead-letter handling**: `retry_failed.py`, see above.
- **Job status tracking**: RQ's own per-job status
  (queued/started/finished/failed), correlated with the
  `AgentRun.id` a `collector_workflow`/`reviewer_workflow` job produces
  via `infrastructure.queue.job_status.get_job_status`.
- **`agent_run` linkage**: `collector_workflow`/`reviewer_workflow` jobs
  return `{"agent_run_id": ...}` from the graph's own state — the graph
  itself (not the job) creates the `AgentRun` row
  (`source_authority`/`temporal_hash_polling` nodes), so there is
  exactly one write path for that table regardless of whether a run was
  triggered by a job or resumed via the admin review API.
- **Structured logs**: `apps/worker/app/jobs/logging.py` — one JSON log
  line per job lifecycle event (`job.started`/`job.completed`/
  `job.skipped`/`job.failed`), always carrying `job_id`, `job_type`, and
  `restaurant_id` so a log aggregator can trace one restaurant's
  activity across every job type it touched.
- **Sync/async bridge**: every job's RQ entry point is a plain sync
  function (RQ has no native asyncio support); the actual DB/graph work
  lives in an internal `async def _run(...)` called via
  `infrastructure.queue.async_bridge.run_async`.

## Testing

`tests/unit/test_job_lock.py`, `test_job_status.py`,
`test_worker_jobs.py` — see `infrastructure/queue/README.md`'s Testing
section for why these monkeypatch each job's `_run` rather than hitting
a real (test) database directly.
