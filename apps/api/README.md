# apps/api/

FastAPI backend. Entry point: `apps/api/app/main.py` (`create_app()` /
module-level `app`).

## Layout

- `app/routers/` — HTTP surface, versioned under `/api/v1`.
  - `health.py` — unversioned health check.
  - `v1/auth/router.py` — `POST /login`, `POST /refresh`, `POST /logout`,
    `POST /logout-all`, `GET /me`. All security events (login
    success/failure, logout, logout-all, refresh) are audit-logged.
  - `v1/admin/router.py` — restaurant/review/ingestion endpoints
    (currently placeholders returning canned responses, but already
    wired to permission checks and audit logging so the pattern is set
    for when real logic lands) plus `GET /audit-log`
    (`Permission.AUDIT_LOG_READ`) and `GET /users` (placeholder).
  - `v1/agents/router.py` — will expose collector/reviewer workflow
    triggers; currently just a ping.
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
- `app/dependencies/` — FastAPI `Depends` wiring: `db.py` (session per
  request), `auth.py` (`CurrentUserDep`, `require_permission(...)`),
  `audit.py` (`AuditServiceDep`), `pagination.py`, `settings.py`.
- `app/core/security.py` — password hashing (bcrypt, called directly
  rather than through passlib — passlib's bcrypt backend detection is
  broken against bcrypt>=4.1), JWT issuance/verification, refresh-token
  hashing. Refresh tokens are tracked server-side by SHA-256 hash (never
  the raw token) so logout/logout-all can actually revoke them before
  natural expiry.
- `app/core/errors.py` — `register_exception_handlers(app)`.
- `app/middleware/request_context.py` — request-scoped context
  (e.g. request IDs for logging).

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
