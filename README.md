# HungrX Agent

Production-ready restaurant data automation platform.

## Structure

- `apps/admin-dashboard` — Next.js + TypeScript admin dashboard
- `apps/api` — FastAPI backend
- `apps/worker` — Background job worker (Redis-backed)
- `workflows/collector_workflow` — LangGraph workflow for data collection
- `workflows/reviewer_workflow` — LangGraph workflow for review/QA
- `core/schemas` — Shared Pydantic schemas
- `core/validation` — Shared validation logic
- `core/config` — Shared configuration
- `database/models` — SQLAlchemy models
- `database/repositories` — Data access layer
- `database/migrations` — Alembic migrations
- `infrastructure/crawler` — Playwright-based crawling infrastructure
- `infrastructure/storage` — Storage adapters
- `infrastructure/queue` — Redis queue adapters
- `tests` — Cross-cutting test suites

## Context for AI assistants / new contributors

See `CLAUDE.md` at the repo root for architecture, key design decisions,
and current build status. Each module listed above also has its own
`README.md` with more detail.

## Getting Started

### 1. Start Docker

Make sure Docker Desktop is running, then start the whole stack (Postgres,
Redis, API, worker, admin dashboard) from the repo root:

```bash
docker compose up -d
```

Check everything is healthy:

```bash
docker compose ps
```

### 2. Apply database migrations

```bash
docker compose exec api uv run alembic -c database/alembic.ini upgrade head
```

### 3. URLs to check

| Service | URL |
|---|---|
| Admin dashboard | http://localhost:3000 |
| API | http://localhost:8000 |
| API docs (Swagger) | http://localhost:8000/docs |

## Development

- **Backend** (API + worker) run inside Docker via `docker compose up -d`
  — code changes are picked up automatically (bind-mounted, hot reload).
  View logs with:

  ```bash
  docker compose logs -f api
  docker compose logs -f worker
  ```

- **Frontend** (admin dashboard) also runs inside Docker at
  http://localhost:3000 with hot reload. To run it standalone outside
  Docker instead:

  ```bash
  cd apps/admin-dashboard
  npm install
  npm run dev
  ```

- **Tests** (Postgres-backed):

  ```bash
  docker compose exec -e TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@postgres:5432/hungrx_test" api uv run pytest tests/ -q
  ```

- **New migration** after changing a model:

  ```bash
  docker compose exec api uv run alembic -c database/alembic.ini revision --autogenerate -m "..."
  docker compose exec api uv run alembic -c database/alembic.ini upgrade head
  ```

- **Reset the database** (drops all data):

  ```bash
  docker compose down -v
  docker compose up -d
  docker compose exec api uv run alembic -c database/alembic.ini upgrade head
  ```
