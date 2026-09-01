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
  4. deterministic_validation — placeholder
  5. human_review       — placeholder
  6. publish            — placeholder
      │
      ▼
ProposedChange → Approval → published Restaurant/Menu data
```

Everything that mutates state goes through `AuditService` so there's a
durable audit trail (see `database/README.md`).

## Module map

| Path | What it is |
|---|---|
| `core/schemas/` | Strict Pydantic domain schemas (Restaurant, Menu, Dish, Nutrition, Source, ProposedChange, AgentRun, audit types, auth/permissions). Source of truth for shapes. |
| `core/config/` | Shared `Settings` (env-driven), logging setup, exception types. |
| `core/validation/` | Shared validation logic (currently minimal). |
| `database/models/` | SQLAlchemy ORM models (Postgres). Mirrors a subset of `core/schemas` for persisted entities. |
| `database/repositories/` | Data-access layer — one repository per model, thin CRUD + query methods, no business logic. |
| `database/migrations/` | Alembic migrations. Always autogenerate + review before applying. |
| `infrastructure/crawler/` | httpx/Playwright fetching, domain locking, robots.txt checks, SHA-256 hashing, snapshot storage. Restricted to verified domains only. |
| `infrastructure/source_authority/` | Resolves a restaurant's verified official website: entity resolution interface, aggregator blocklist, URL normalization, domain validation. |
| `infrastructure/storage/` | `StorageAdapter` interface + local filesystem implementation for snapshot blobs. |
| `infrastructure/ai/` | `AIProvider` interface + `OpenAIProvider` implementation — strict structured-output-only AI calls, swappable model backend. |
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

## Running things

- Stack: `docker compose up -d` (see `docker-compose.yml` /
  `docker-compose.override.yml` for dev bind mounts).
- Tests: `docker compose exec -e TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@postgres:5432/hungrx_test" api uv run pytest tests/ -q`
- Migrations: `docker compose exec api uv run alembic -c database/alembic.ini revision --autogenerate -m "..."`, then `upgrade head` — always review the generated file before applying.
- Tests are Postgres-backed (not SQLite/mocks) via `tests/conftest.py`'s
  `db_session` fixture (savepoint-per-test rollback). External
  dependencies (e.g. entity resolution providers) are faked via real
  implementations of the relevant interface, not mocked.

## Status (as of 2026-09-01)

Done: audit system, crawler infrastructure, core Pydantic schemas, source
authority module, LangGraph state/graph skeleton, Collector Agent 1
(Source Authority), Agent 2 (Extraction — capture/persist only, no AI
interpretation), and Agent 3 (Multimodal Translation — AI structured
extraction via `infrastructure/ai/`) — all fully implemented and tested.

Not yet built: Collector Agents 4–6 (Deterministic Validation, Human
Review, Publish) — currently placeholder nodes in
`workflows/collector_workflow/nodes/`; reviewer workflow (entirely
skeleton); worker job processing (placeholder loop only). `build_graph()`
now also requires `storage` (StorageAdapter) and `ai_provider`
(AIProvider) arguments since Extraction persists crawl captures and
Multimodal Translation calls the AI provider through them.
