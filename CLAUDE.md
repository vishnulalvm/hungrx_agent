# hungrX Agent — Project Context

Restaurant data automation platform: crawls restaurant websites, extracts
structured menu/nutrition data via AI, runs it through deterministic
validation and human review, and publishes it. Built as a Python monorepo
(FastAPI + LangGraph + SQLAlchemy/Postgres + Redis) with a Next.js admin
dashboard.

This file is the entry point for any AI/CLI picking up this repo cold.
Each major module also has its own `README.md` with more detail — read
this first, then the module README for whatever you're touching.

## Architecture at a glance

```
Restaurant identity
      │
      ▼
Source Authority (infrastructure/source_authority) ─▶ verified official URL, Source row
      │
      ▼
Crawler (infrastructure/crawler) ─▶ HTML/PDF snapshot, hashed & stored
      │
      ▼
Collector LangGraph workflow (workflows/collector_workflow)
  1. source_authority  — DONE (real implementation)
  2. extraction        — DONE (real implementation; capture only, no AI)
  3. multimodal_translation — DONE (AI translation via infrastructure/ai/)
  4. deterministic_validation — DONE (rule engine in core/validation/, no LLM)
  5. human_review       — DONE (pauses via LangGraph interrupt(); see below)
  6. publish            — DONE (writes production tables only on APPROVED)
      │
      ▼ (pause — durable across requests, via AsyncPostgresSaver)
Admin review API (GET/POST /api/v1/admin/reviews/...) ──▶ approve / reject / edit-then-approve
      │ (resumes the paused graph via Command(resume=...))
      ▼
ProposedChange → Approval → published Restaurant/Menu data (database/models/restaurant.py)

Reviewer LangGraph workflow (workflows/reviewer_workflow) — checks an
already-published restaurant's source for drift, on demand
  1. temporal_hash_polling  — DONE (Change Detection: loads active source, SHA-256 compare, persists snapshot, records AgentRun.metrics; early-stops the run when unchanged)
  2. targeted_reextraction  — DONE (Agent 7 part 1: reuses collector's capture/AI-structuring path; carries source_snapshot_ids forward as reextraction_source_refs)
  3. json_delta_generation  — DONE (Agent 7 part 2: compares against production data via RestaurantRepository.get_full_tree, DeepDiff-based, produces core/schemas/diff.py's JSONDelta with source references; never writes)
  4. delta_validation       — DONE (Agent 8 part 1: full-record validation, but reported issues scoped to changed/new dishes via state["delta"])
  5. human_final_sync       — DONE (Agent 8 part 2: pauses via LangGraph interrupt(); reuses ProposedChange/Approval)
  6. publish                — DONE (Agent 8 part 2 cont.: PATCH-style — only delta-named rows touched, via nodes/delta_patch.py; only on APPROVED)
```

**Human-in-the-loop, concretely**: `human_review_node` creates a
`ProposedChange` row (PENDING) and calls LangGraph's `interrupt()`,
which suspends the run — the API request that triggered the collector
run returns with the run "paused," not "finished." An admin later calls
`GET /api/v1/admin/reviews` (pending queue), `GET .../reviews/{id}`
(detail), then one of `POST .../approve`, `.../reject`, or
`.../edit-approve`. Each of those calls `ReviewService`, which resumes
the *same* graph run (looked up by `ProposedChange.thread_id`, which
equals the original `agent_run_id`) via `graph.ainvoke(Command(resume=...),
config)`. Only `approve`/`edit_then_approve` route to `publish_node`,
which is the only code in the repo permitted to write to the
`restaurants`/`menus`/`menu_categories`/`dishes` tables — see
`workflows/collector_workflow/README.md` for the full node-by-node
breakdown and `infrastructure/checkpointer.py` for why a real
Postgres-backed checkpointer (not the in-memory default) is required for
this to survive across separate HTTP requests.

Everything that mutates state goes through `AuditService` so there's a
durable audit trail (see `database/README.md`).

## Module map

| Path | What it is |
|---|---|
| `core/schemas/` | Strict Pydantic domain schemas (Restaurant, Menu, Dish, Nutrition, Source, ProposedChange, AgentRun, audit types, auth/permissions). Source of truth for shapes. |
| `core/config/` | Shared `Settings` (env-driven), logging setup, exception types. |
| `core/validation/` | Deterministic (no-LLM) validation engine: schema, nutrition/Atwater, allergen taxonomy, price, required-field, duplicate, and impossible-value checks, plus safe (formatting-only) corrections. |
| `database/models/` | SQLAlchemy ORM models (Postgres), including the production `restaurants`/`menus`/`menu_categories`/`dishes` tables (written only by the collector/reviewer workflows' respective `publish` nodes), the `proposed_changes`/`approvals` review-queue tables, and `source_snapshots` (durable `SourceSnapshot` persistence — the reviewer workflow's Temporal Hash Polling needs "what was the hash last time" to survive across separate runs, unlike the collector workflow's in-memory-only snapshots). |
| `database/repositories/` | Data-access layer — one repository per model, thin CRUD + query methods, no business logic. |
| `database/migrations/` | Alembic migrations. Always autogenerate + review before applying. |
| `infrastructure/crawler/` | httpx/Playwright fetching, domain locking, robots.txt checks, SHA-256 hashing, snapshot storage. Restricted to verified domains only. |
| `infrastructure/source_authority/` | Resolves a restaurant's verified official website: entity resolution interface, aggregator blocklist, URL normalization, domain validation. |
| `infrastructure/storage/` | `StorageAdapter` interface + local filesystem implementation for snapshot blobs. |
| `infrastructure/ai/` | `AIProvider` interface + `OpenAIProvider` implementation — strict structured-output-only AI calls, swappable model backend. |
| `infrastructure/checkpointer.py` | `get_checkpointer(settings)` — the durable (Postgres-backed) LangGraph checkpointer that makes human-in-the-loop pause/resume survive across separate API requests. |
| `infrastructure/queue/` | RQ/Redis background-job plumbing: named queues + retry policy, the per-restaurant dedup lock, job-status/AgentRun correlation, and the sync-RQ/async-app bridge. See `infrastructure/queue/README.md`. |
| `workflows/collector_workflow/` | LangGraph state machine that runs a restaurant through Source Authority → Extraction → ... → Publish. |
| `workflows/reviewer_workflow/` | LangGraph state machine that checks an already-published restaurant's source for drift (Temporal Hash Polling → ... → Human Final Sync → Publish), early-stopping when the source hash hasn't changed. Reuses the collector workflow's capture/AI-structuring path and the same ProposedChange/Approval review-queue infrastructure. |
| `apps/api/` | FastAPI backend: auth, admin, agents, mobile routers; services (audit, auth, source authority). |
| `apps/worker/` | Background job worker (RQ over Redis): restaurant ingestion, source crawling, the collector workflow, maintenance polling, the reviewer workflow, and dead-letter retry sweeps — one job type per module under `app/jobs/`. See `apps/worker/README.md`. |
| `apps/admin-dashboard/` | Next.js 15 + TypeScript admin UI. The browser never holds a bearer token — this app's own Route Handlers (`src/app/api/auth/*`, `src/app/api/proxy/[...path]`) sit between the browser and FastAPI, storing the JWT pair as httpOnly cookies. See `apps/admin-dashboard/README.md`. |
| `tests/` | Cross-cutting unit/integration tests, Postgres-backed via `tests/conftest.py`. |

## Key architectural decisions (read before changing these areas)

- **`workflows/` is named that, not `langgraph/`, on purpose.** A package
  literally named `langgraph/` at the repo root shadows the real
  `langgraph` PyPI library on `sys.path` (repo root resolves before
  site-packages inside the containers), silently breaking every
  `from langgraph.graph import StateGraph` import. Never recreate a
  top-level `langgraph/` directory.
- **Every domain schema in `core/schemas/` uses `extra="forbid"`.** This
  is deliberate: AI extraction must never be able to inject an arbitrary
  key (especially into `Nutrition`/`Macros`/`Micronutrients`) that then
  flows into the database unvalidated.
- **"Never hallucinate URLs" is enforced structurally, not by
  instruction.** In `workflows/collector_workflow/nodes/source_authority.py`,
  the node's returned state update only contains `source_url`/`source`
  keys when `SourceAuthorityService` returns `ResolutionStatus.VERIFIED`
  — which itself only happens after a `Source` row has actually been
  persisted. Every other status (`NOT_FOUND`, `REJECTED`, `NEEDS_REVIEW`)
  omits those keys entirely rather than setting them to `None` or a guess.
- **Audit logging never commits, only flushes.** `AuditService.log(...)`
  calls `session.flush()`, never `session.commit()`, so an audit row is
  always part of the same transaction as the business change it
  describes — they succeed or roll back together. Callers own the
  commit.
- **Crawler is domain-locked.** `DomainVerifier`/`DomainLock` in
  `infrastructure/crawler/domain_lock.py` hard-restrict fetching to a
  single verified domain (host-exact match, no subdomain/lookalike
  bypass) plus per-domain rate limiting. The same domain-lock logic is
  reused by `infrastructure/source_authority/domain_validator.py`.
- **AgentRun lifecycle spans the whole collector run, not one node.**
  `AgentRunRepository.create()` starts a run `RUNNING`; only whichever
  node completes the *entire* pipeline should call `mark_succeeded`.
  Individual node failures call `mark_failed` and stop that run.
- **AI calls are strict-structured-output only, via `infrastructure/ai/AIProvider`.**
  `AIProvider.generate_structured` takes a Pydantic model type
  (`core/schemas/extraction_output.py`'s `ExtractionOutput`, currently)
  and can only ever return an instance of it — `OpenAIProvider` enforces
  this at the API level via `response_format=<model>` (OpenAI's strict
  `json_schema` mode), not by prompt instruction. The AI-only output
  schema is deliberately separate from the "real" domain schemas
  (`core/schemas/restaurant.py`/`menu.py`) — no `id` fields the model
  could collide with real primary keys, and every extracted item carries
  `confidence`/`source_snapshot_ids`. Mapping AI output into real domain
  objects (assigning ids, merging onto the caller-known `Restaurant`)
  happens in Python in the calling node, never inside the model's
  output. No AI node has access to a restaurant/menu/dish repository —
  only `AgentRun`/`AuditLog`, so an AI call can never itself write
  business data to the database.
- **Deterministic validation never silently changes AI-generated data.**
  `core/validation/engine.py`'s `validate()` only ever rewrites a value
  via `core/validation/safe_corrections.py`, and that module is
  restricted to pure formatting/normalization changes (whitespace
  collapse, currency-code casing, exact-duplicate removal) — it never
  touches a numeric, monetary, or AI-inferred field (calories, price,
  allergens, description content, etc.). Everything else the validator
  finds becomes a `ValidationIssue` (error or warning) for a human to
  act on, never an automatic rewrite. Every correction actually applied
  is recorded on `ValidationOutcome.corrected_fields` with old/new
  values, so corrections are visible, not silent.
- **Human-in-the-loop pause/resume uses a real, durable LangGraph
  checkpointer — never `MemorySaver`.** The API request that pauses a
  collector run at `human_review` (via LangGraph's `interrupt()`) is
  never the same request that later resumes it after an admin's
  decision, so the paused state has to survive on something other than
  process memory. `infrastructure/checkpointer.py`'s `AsyncPostgresSaver`
  (from `langgraph-checkpoint-postgres`) persists it to the same Postgres
  database as everything else. `build_graph()` requires a `checkpointer`
  argument with no unsafe default for the same reason `storage`/
  `ai_provider` have none.
- **`human_review_node`'s ProposedChange creation is idempotent by
  `thread_id` lookup, not by anything carried on state.** LangGraph
  replays a node function from the top on resume, with the *same* input
  state as before the interrupt (nothing the node itself returned after
  its own `interrupt()` call is part of that replay's input) — a naive
  "have I already created this" check against state would create a
  second `ProposedChange` row on every resume. `ProposedChangeRepository.get_by_thread_id`
  is the actual guard. See `workflows/collector_workflow/nodes/human_review.py`'s
  docstring and `tests/unit/test_human_review_node.py`'s
  `TestIdempotentRecordCreation` for the specific bug this prevents.
- **"Do not allow unapproved data into production tables" is enforced at
  two independent layers.** (1) Graph topology: only
  `human_approval_status == ProposedChangeStatus.APPROVED` — set
  exclusively from a real resumed admin decision, never a default —
  routes to `publish_node`. (2) `publish_node` itself re-checks that
  status before writing anyway (defense in depth against a routing bug
  elsewhere), re-validates the data immediately before commit (deterministic
  `core.validation.validate()`, refusing on any error — approval of *some*
  version of the data is not proof the final payload still passes), and
  `RestaurantRepository.persist_tree`/its own write path is called nowhere
  except that one node. This holds independently for both workflows: the
  collector workflow's `workflows/collector_workflow/nodes/publish.py`
  (always inserts a brand-new restaurant; refuses to republish an
  already-published `entity_id`) and the reviewer workflow's
  `workflows/reviewer_workflow/nodes/publish.py` (always updates an
  already-published restaurant in place; refuses if no production row
  exists yet) are deliberately separate implementations of the same
  guarantee, not a shared node — their write semantics (insert-only vs.
  update-in-place) are opposite by design.
- **The reviewer workflow's publish node applies PATCH-style, not a full
  tree replace.** `workflows/reviewer_workflow/nodes/delta_patch.py`'s
  `apply_patch` walks the *approved* `JSONDelta`'s ADDED/REMOVED/CHANGED
  entries and touches only the specific restaurant-level scalar columns
  and dish rows the delta actually names — an untouched dish's row is
  never flushed, let alone deleted and recreated with a new physical
  identity. This still runs inside the caller's existing session (no
  commit inside the node), so a failure partway through leaves nothing
  partially applied once the caller rolls back — the collector workflow's
  publish node has no equivalent concern since it always inserts a
  brand-new tree wholesale.
- **`ReviewService` (`apps/api/app/services/review_service.py`) resolves
  which workflow's graph to resume via `AgentRun.workflow_type`, looked
  up through `ProposedChange.agent_run_id`.** A `ProposedChange` row
  itself doesn't record which workflow created it, and the collector and
  reviewer workflows' graphs have entirely different node names/topology
  — resuming the wrong one fails outright rather than silently doing the
  wrong thing, but getting the dispatch right matters for every
  `/api/v1/admin/reviews/{id}/approve|reject|edit-approve` call to work
  at all for a reviewer-workflow-paused run. Defaults to the collector
  workflow's graph when no `AgentRun` is found (a human-authored
  `ProposedChange` with no `agent_run_id`), preserving this service's
  original behavior for that case.
- **The reviewer workflow's early-stop gate is graph routing, not an
  error.** `temporal_hash_polling`'s `hash_changed == False` is a normal,
  successful outcome (the `AgentRun` completes `SUCCEEDED`) — it's
  `workflows/reviewer_workflow/graph.py`'s `_route_after_hash_polling`
  conditional edge that sends the run straight to `END` rather than into
  `targeted_reextraction`. Everything downstream of that gate (re-extraction,
  an AI call, diffing, an interrupt) only runs when the source's SHA-256
  hash actually differs from the last recorded `source_snapshots` row for
  that source — see `database/repositories/source_snapshot_repository.py`,
  which exists specifically because the collector workflow's `SourceSnapshot`s
  never had durable persistence (in-memory LangGraph state only), and a
  hash comparison across separate reviewer runs needs one.
- **Background jobs never pre-seed graph state beyond a bare identity —
  the graph always re-derives its own source/extraction state.**
  `apps/worker/app/jobs/collector_workflow.py` and `reviewer_workflow.py`
  invoke their graph from `START` with only a `Restaurant` on state; they
  do not pass through the `source_id`/`source_snapshot_id` a preceding
  `source_crawl`/`maintenance_polling` job produced, because
  `source_authority_node`/`temporal_hash_polling_node` already
  re-resolve/re-verify from the database on every run rather than
  trusting caller-supplied state (see those nodes' own docstrings) — a
  job passing a possibly-stale value through would just be trusting the
  same kind of caller-supplied hint those nodes were specifically built
  to distrust. `source_crawl`'s own root-page capture and the collector
  workflow's `extraction` node capturing menu/nutrition pages are
  therefore deliberately non-duplicative (different pages, different
  purposes), not a redundant double-crawl.
- **Per-restaurant job dedup is a Redis lock, not a database check.**
  `infrastructure/queue/lock.py`'s `RestaurantJobLock` (`SET NX PX`,
  keyed `(restaurant_id, job_type)`) is what every job in
  `apps/worker/app/jobs/` acquires before doing any work, rather than
  querying for an existing `RUNNING` `AgentRun` — a DB check has a race
  window between "check" and "the graph's own node creates its
  `AgentRun` row" that a job-level lock acquired before any work starts
  does not. TTL-based (no heartbeat/lock-extension) so a crashed
  worker's lock self-heals rather than wedging that restaurant
  permanently; deliberately tolerates a stale lock expiring a little
  early over adding that complexity.
- **SSRF protection is layered, not a single check.** A restaurant's
  "official domain" being on `DomainVerifier`'s allow-list only
  restricts *which hostname* the crawler targets — a hostname passing
  that check can still resolve to an internal/cloud-metadata address
  (either because the official URL was an IP literal, or via DNS
  rebinding: a public IP at verification time, an internal one at
  connection time). `infrastructure/crawler/ssrf_guard.py` is checked at
  two independent points: `infrastructure/source_authority/
  url_normalizer.py`/`domain_validator.py` reject an IP-literal host
  outright at verification time (cheap, resolution-free — a real
  restaurant domain is never a bare IP), and `infrastructure/crawler/
  http_fetcher.py`'s `HttpFetcher.fetch()` re-resolves and checks the
  *actual* target address immediately before every connection —
  including every redirect hop, since `follow_redirects=True` would
  otherwise let a same-domain-verified site's redirect chain end at an
  internal address with zero re-validation. Redirects are therefore
  followed manually, one hop at a time, each re-checked against both
  `DomainVerifier` and `ssrf_guard`; capped at 10 hops. `HttpFetcher`
  also caps a single response to 25MB (`Content-Length` pre-check plus a
  running counter while streaming) so an oversized/slow-trickling
  response can't exhaust worker memory. `BrowserFetcher` (Playwright)
  does not yet have equivalent per-hop redirect validation — Playwright
  follows redirects internally with no interception hook wired up here —
  so it inherits only the allow-list check on the initial URL; it is not
  the crawler's default path (`HttpFetcher` is), but closing this gap
  for JS-rendered pages is a known follow-up, not yet done.
- **Login/refresh are rate-limited; nothing else is.** `apps/api/app/
  core/rate_limit.py`'s `rate_limit_login`/`rate_limit_refresh` are
  Redis fixed-window counters (`INCR`+`EXPIRE`, no new dependency —
  Redis is already required for RQ) applied only to `POST /auth/login`
  (by IP and, independently, by the attempted email — so neither
  "many emails from one IP" nor "one email from many IPs" alone escapes
  it) and `POST /auth/refresh` (by IP). Every other mutating endpoint
  already requires a valid access token first, so brute-forcing them
  isn't the same class of problem. Deliberately a no-op when
  `settings.environment == "test"` (see `tests/conftest.py`, which sets
  that env var before any app import) since the test suite's ASGI
  transport gives every request the same "client IP." `Settings` also
  fails fast at construction (`_reject_weak_secret_in_production`) if
  `environment=production` and `api_secret_key` is still the
  committed-in-source `"change-me"` default or otherwise implausibly
  short — anyone who has read this repo knows that default, so booting
  production with it unchanged would let anyone forge access tokens
  (including `role=SUPER_ADMIN`); `create_app()` similarly refuses to
  start with `CORS_ORIGINS=*` in production.

## Running things

- Stack: `docker compose up -d` (see `docker-compose.yml` /
  `docker-compose.override.yml` for dev bind mounts).
- Tests: `docker compose exec -e TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@postgres:5432/hungrx_test" api uv run pytest tests/ -q`
- Migrations: `docker compose exec api uv run alembic -c database/alembic.ini revision --autogenerate -m "..."`, then `upgrade head` — always review the generated file before applying.
- Tests are Postgres-backed (not SQLite/mocks) via `tests/conftest.py`'s
  `db_session` fixture (savepoint-per-test rollback) and, for anything
  touching human_review's interrupt/resume, its `checkpointer` fixture
  (a real `AsyncPostgresSaver` against `TEST_DATABASE_URL` — an
  in-memory checkpointer wouldn't prove anything about durability).
  External dependencies (e.g. entity resolution providers) are faked via
  real implementations of the relevant interface, not mocked. Note:
  checkpoint rows written via the `checkpointer` fixture live outside
  `db_session`'s rolled-back transaction (separate psycopg connection)
  and are not automatically cleaned up — tests use a fresh random
  `thread_id` per test to stay isolated regardless.

## Status (as of 2026-09-01)

Done: audit system, crawler infrastructure, core Pydantic schemas, source
authority module, LangGraph state/graph skeleton, and all six Collector
agents — Source Authority, Extraction (capture/persist only, no AI
interpretation), Multimodal Translation (AI structured extraction via
`infrastructure/ai/`), Deterministic Validation (rule engine in
`core/validation/`, no LLM involved), Human Review (LangGraph
interrupt/resume, backed by `infrastructure/checkpointer.py`), and
Publish (writes `database/models/restaurant.py`'s production tables,
reachable only on an approved review) — all fully implemented and
tested, plus the admin review API
(`apps/api/app/routers/v1/admin/router.py`'s `/reviews` endpoints via
`apps/api/app/services/review_service.py`).

Also done: all six Reviewer Workflow agents — Temporal Hash Polling
(SHA-256 re-check against `source_snapshots`, the early-stop gate),
Targeted Re-Extraction (reuses the collector workflow's capture/AI path,
mapped onto the currently published restaurant), JSON Delta Generation
(DeepDiff-based, produces `core/schemas/diff.py`'s `JSONDelta`), Delta
Validation (same `core/validation/` engine), Human Final Sync (reuses
the collector workflow's `ProposedChange`/interrupt-resume review-queue
infrastructure), and Publish (updates an already-published restaurant,
distinct write semantics from the collector workflow's insert-only
publish node) — see `workflows/reviewer_workflow/README.md` for the
full node-by-node breakdown. (At the time this section was originally
written, nothing triggered a reviewer run automatically; see the
background-job-processing paragraph below — `maintenance_polling` now
does, via a periodic sweep.)

Also done: background job processing (`apps/worker/`, RQ over Redis) —
`restaurant_ingestion` (Source Authority resolution for a brand-new
restaurant identity), `source_crawl` (verified-source root-page capture
+ SourceSnapshot persistence), `collector_workflow` and
`reviewer_workflow` (run the respective graph from `START`, returning
once the run pauses at its own `interrupt()` or finishes),
`maintenance_polling` (the periodic sweep enumerating every published
restaurant and enqueueing one `reviewer_workflow` job each — this is
what now actually triggers reviewer runs, previously "no scheduler/cron
wired up"), and `retry_failed` (sweeps every queue's dead-letter
`FailedJobRegistry`, requeueing only transient-looking failures). Every
job is deduped per-restaurant via `infrastructure/queue/lock.py`'s
Redis `SET NX PX` lock (`restaurant_ingestion` dedupes on a
caller-supplied seed id instead, since no restaurant id exists yet at
that stage) and structured-logs its lifecycle
(`apps/worker/app/jobs/logging.py`). See `apps/worker/README.md` and
`infrastructure/queue/README.md` for the full breakdown.

Also done: the admin dashboard (`apps/admin-dashboard/`) is connected to
the real backend — auth (login/logout/refresh, all proxied through this
app's own Route Handlers so the browser never holds the bearer token),
protected routes (`middleware.ts`), and typed React Query hooks for
restaurant management, ingestion triggering, the review queue
(approve/reject/edit-then-approve), agent run status, and the audit log.
This closed two real backend gaps that existed only as placeholders
before: `GET /admin/restaurants`(`/{id}`) and `GET /agents/runs`(`/{id}`)
are now real reads (`RestaurantRepository`/`AgentRunRepository`'s new
`list_paginated` methods), and `POST /admin/ingestion/trigger` now
actually enqueues `apps/worker/app/jobs/restaurant_ingestion.py`'s RQ
job rather than returning a canned response. `POST /admin/restaurants`
(the old placeholder) was removed outright rather than implemented —
there is deliberately no direct restaurant-create endpoint; the only
write path into the production restaurant tables remains an approved
review, per the two-layer publish guarantee above. See
`apps/admin-dashboard/README.md` and `apps/api/README.md`.

`build_graph()` (both workflows) requires `storage`
(StorageAdapter), `ai_provider` (AIProvider), and `checkpointer`
(BaseCheckpointSaver) — none has an unsafe silent default.
`workflows/collector_workflow/dependencies.py` provides the
process-default `storage`/`ai_provider` instances `ReviewService` uses
when resuming a review (its `_LazyOpenAIProvider` defers OpenAI
API-key validation until actually called, since resuming a paused
human_review/human_final_sync never re-reaches the AI-calling node);
the reviewer workflow's nodes reuse these same defaults rather than
duplicating them.
