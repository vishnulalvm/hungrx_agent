# tests/

Cross-cutting test suites for `core/`, `database/`, `infrastructure/`,
and `workflows/`. (`apps/api/tests/` and `apps/worker/tests/` hold
app-local tests and are separate pytest roots — see `pyproject.toml`'s
`testpaths`.)

## Conventions

- **Postgres-backed, not SQLite, not mocked.** `conftest.py`'s
  `db_session` fixture wraps each test in a savepoint that's rolled back
  afterward, so tests run against a real Postgres database
  (`TEST_DATABASE_URL`) with zero cross-test pollution and zero
  SQLite/Postgres behavioral drift.
- **Fakes over mocks for external interfaces.** e.g.
  `test_source_authority_node.py` defines a local `FakeProvider(
  EntityResolutionProvider)` that implements the real ABC, rather than
  mocking `SourceAuthorityService`. This means tests exercise the actual
  interface contract, not an assumption about how it's called.
- Run via:
  ```
  docker compose exec -e TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@postgres:5432/hungrx_test" api uv run pytest tests/ -q
  ```

## Layout

- `unit/` — one file per module under test, mirrors the source tree
  loosely (e.g. `test_domain_lock.py` ↔ `infrastructure/crawler/domain_lock.py`,
  `test_source_authority_node.py` ↔
  `workflows/collector_workflow/nodes/source_authority.py`).
- `integration/` — multi-component flows: `test_audit_trail.py`,
  `test_auth_flow.py`, `test_authorization.py`,
  `test_source_authority_service.py`.
- `e2e/` — reserved for full end-to-end tests; empty so far.
