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
| `database/models/` | SQLAlchemy ORM models (Postgres), including the production `restaurants`/`menus`/`menu_categories`/`dishes` tables (written only by `publish_node`) and the `proposed_changes`/`approvals` review-queue tables. |
| `database/repositories/` | Data-access layer — one repository per model, thin CRUD + query methods, no business logic. |
| `database/migrations/` | Alembic migrations. Always autogenerate + review before applying. |
| `infrastructure/crawler/` | httpx/Playwright fetching, domain locking, robots.txt checks, SHA-256 hashing, snapshot storage. Restricted to verified domains only. |
| `infrastructure/source_authority/` | Resolves a restaurant's verified official website: entity resolution interface, aggregator blocklist, URL normalization, domain validation. |
| `infrastructure/storage/` | `StorageAdapter` interface + local filesystem implementation for snapshot blobs. |
| `infrastructure/ai/` | `AIProvider` interface + `OpenAIProvider` implementation — strict structured-output-only AI calls, swappable model backend. |
| `infrastructure/checkpointer.py` | `get_checkpointer(settings)` — the durable (Postgres-backed) LangGraph checkpointer that makes human-in-the-loop pause/resume survive across separate API requests. |
| `infrastructure/queue/` | Redis queue adapter interface (worker job queue). |
| `workflows/collector_workflow/` | LangGraph state machine that runs a restaurant through Source Authority → Extraction → ... → Publish. |
| `workflows/reviewer_workflow/` | Second LangGraph workflow for review/QA — skeleton only, not yet built. |
| `apps/api/` | FastAPI backend: auth, admin, agents, mobile routers; services (audit, auth, source authority). |
| `apps/worker/` | Background job worker (Redis-backed) — currently a placeholder run loop. |
| `apps/admin-dashboard/` | Next.js + TypeScript admin UI (separate frontend app). |
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
  elsewhere), and `RestaurantRepository` (the only code that writes
  `restaurants`/`menus`/`menu_categories`/`dishes`) is imported nowhere
  except that one node.

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

Not yet built: the reviewer workflow (entirely skeleton — QA on already-
published data, distinct from the collector workflow's human review
step); worker job processing (placeholder loop only — nothing currently
triggers a collector run automatically, it has to be invoked directly).
`build_graph()` requires `storage` (StorageAdapter), `ai_provider`
(AIProvider), and `checkpointer` (BaseCheckpointSaver) — none has an
unsafe silent default. `workflows/collector_workflow/dependencies.py`
provides the process-default `storage`/`ai_provider` instances
`ReviewService` uses when resuming a review (its `_LazyOpenAIProvider`
defers OpenAI API-key validation until actually called, since resuming
a paused human_review never re-reaches multimodal_translation).
