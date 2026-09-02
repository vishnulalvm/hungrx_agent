# apps/api/

FastAPI backend. Entry point: `apps/api/app/main.py` (`create_app()` /
module-level `app`).

## Layout

- `app/routers/` — HTTP surface, versioned under `/api/v1`.
  - `health.py` — unversioned health check.
  - `v1/auth/router.py` — `POST /login`, `POST /refresh`, `POST /logout`,
    `POST /logout-all`, `GET /me`. All security events (login
    success/failure, logout, logout-all, refresh) are audit-logged.
  - `v1/admin/router.py` — `GET /restaurants` (paginated summaries via
    `RestaurantRepository.list_paginated`) and `GET /restaurants/{id}`
    (full tree via `get_full_tree`, 404 if unpublished) are real reads;
    there is deliberately no `POST /restaurants` — the only write path
    into the production restaurant tables is an approved review (see
    below), never a direct create. `POST /ingestion/trigger`
    (`Permission.INGESTION_TRIGGER`) is real: enqueues
    `apps/worker/app/jobs/restaurant_ingestion.py`'s RQ job by dotted
    import-path string (not a direct Python import — the api process's
    image never includes `apps/worker`, see `apps/api/Dockerfile`'s
    `COPY` list; RQ only resolves that path once a worker actually
    dequeues the job) and audit-logs the trigger, returning the RQ
    `job_id` for the caller to correlate/poll. The review queue is
    **fully implemented**: `GET /reviews` (pending list), `GET
    /reviews/{id}` (detail), `POST /reviews/{id}/approve`, `POST
    /reviews/{id}/reject`, `POST /reviews/{id}/edit-approve` — see
    `workflows/collector_workflow/README.md`'s Human Review/Publish
    section for the full pause/resume mechanics these endpoints drive.
    Also `GET /audit-log` (`Permission.AUDIT_LOG_READ`) and `GET /users`
    (placeholder).
  - `v1/agents/router.py` — `GET /runs` (paginated, most-recent-first via
    `AgentRunRepository.list_paginated`) and `GET /runs/{id}` (404 if
    unknown) — read-only status for collector/reviewer workflow runs.
    Deliberately no trigger endpoint here: an `AgentRun` is only ever
    created by a graph's own `source_authority`/`temporal_hash_polling`
    node, reachable via `/admin/ingestion/trigger` (collector) or the
    worker's `maintenance_polling` sweep (reviewer) — never directly, so
    there's exactly one path that creates that row per workflow type.
  - `v1/mobile/router.py` — placeholder surface for the mobile client.
- `app/services/` — business logic that routers/nodes call into.
  - `audit_service.py` — `AuditService(session).log(...)` /
    `.log_security_event(...)`. **Flushes, never commits** — audit rows
    ride in the same transaction as whatever change they describe. Every
    new mutating endpoint or workflow node should call this.
  - `auth_service.py` — login, token refresh, logout (`logout()` returns
    the resolved `User | None` so callers can attribute an audit row
    without an extra query), logout-all.
  - `source_authority_service.py` — see
    `infrastructure/source_authority/README.md`; this is the
    orchestration layer, the actual validation/normalization logic lives
    in `infrastructure/source_authority/`.
  - `review_service.py` — `ReviewService`: business logic behind the
    review-queue endpoints, shared by both the collector and reviewer
    workflows. `approve`/`reject`/`edit_then_approve` all write an
    `Approval` row and an audit row *first* (in the same request/
    transaction, so the API response is consistent even before the graph
    finishes resuming), then resume the paused run via
    `graph.ainvoke(Command(resume=decision), config={"configurable":
    {"thread_id": proposed_change.thread_id}})`. **Which graph** is
    resolved per-call via `_graph_builder_for`: looks up the
    `ProposedChange`'s `AgentRun` (through `agent_run_id`) and checks
    `AgentRun.workflow_type` — `REVIEWER` resumes
    `workflows/reviewer_workflow/graph.py`'s graph, anything else (or no
    `AgentRun` found) falls back to the collector workflow's, since a
    `ProposedChange` row itself doesn't record which workflow produced
    it and the two graphs have entirely different node topology (see
    `tests/integration/test_reviewer_human_in_the_loop.py` for the
    end-to-end proof, and this module's own docstring for the bug this
    dispatch fixed). Every action re-checks the `ProposedChange` is
    still `PENDING` first (`409 Conflict` otherwise) so a double-submit
    can't resume the same paused run twice. Builds its own checkpointer/
    graph per call via `infrastructure/checkpointer.py` and
    `workflows/collector_workflow/dependencies.py`'s process defaults
    (reused as-is by the reviewer workflow's graph too — both take the
    same `storage`/`ai_provider` shape).
- `app/dependencies/` — FastAPI `Depends` wiring: `db.py` (session per
  request), `auth.py` (`CurrentUserDep`, `require_permission(...)`),
  `audit.py` (`AuditServiceDep`), `review.py` (`ReviewServiceDep`),
  `pagination.py`, `settings.py`.
- `app/core/security.py` — password hashing (bcrypt, called directly
  rather than through passlib — passlib's bcrypt backend detection is
  broken against bcrypt>=4.1), JWT issuance/verification, refresh-token
  hashing. Refresh tokens are tracked server-side by SHA-256 hash (never
  the raw token) so logout/logout-all can actually revoke them before
  natural expiry.
- `app/core/errors.py` — `register_exception_handlers(app)`.
- `app/core/rate_limit.py` — Redis fixed-window rate limiting for
  `POST /auth/login` (`rate_limit_login`, by IP and by attempted email
  independently) and `POST /auth/refresh` (`rate_limit_refresh`, by IP)
  — the two unauthenticated, token-issuing endpoints a brute-force/
  credential-stuffing attempt actually targets. No-op when
  `settings.environment == "test"` (see `tests/conftest.py`). A 429
  (`RateLimitedError` in `core/config/exceptions.py`) uses the same
  `AppError` envelope as every other error response.
- `app/middleware/request_context.py` — request-scoped context
  (e.g. request IDs for logging); an inbound `X-Request-ID` is
  length/charset-sanitized (`_sanitize_request_id`) before being echoed
  into logs/the response, rather than accepted verbatim.

## Pagination

`core/schemas/common.py`'s `PaginatedResponse[T]`/`PaginationParams` and
`app/dependencies/pagination.py`'s `PaginationDep` are the shared shape
for every paginated list endpoint (`/admin/restaurants`, `/agents/runs`
so far). Note `PaginatedResponse` is written with `typing.Generic`/
`TypeVar`, not PEP 695's `class Foo[T]` syntax — this codebase's
containers run Python 3.11, which doesn't support PEP 695 generics at
all (that syntax error was latent/undiscovered here until this class was
first actually imported by a real endpoint).

## Permissions

`Permission` enum lives in `core/schemas/auth.py`, not here. Check there
before adding a new permission-gated endpoint — role-to-permission
mappings are defined alongside the enum.

## Auth model

Short-lived access tokens (default 60 min) carry the user's role so
authorization doesn't need a DB round trip per request. Refresh tokens
are long-lived but revocable (hash stored in `refresh_tokens` table).
See the module-level docstring in `app/core/security.py` for the full
threat-model reasoning.

## Production startup guards

`core/config/settings.py`'s `Settings` fails fast at construction if
`environment=production` and `api_secret_key` is still the
committed-in-source `"change-me"` default (or otherwise under 32
characters) — that default is public (it's in this repo), so booting
production with it unchanged would let anyone forge access tokens for
any role. `create_app()` (`app/main.py`) similarly refuses to start if
`CORS_ORIGINS=*` in production (wildcard origins combined with
`allow_credentials=True` is a misconfiguration risk even though a
spec-compliant browser won't actually honor `*` with credentials).
Neither guard affects development/staging/test.
