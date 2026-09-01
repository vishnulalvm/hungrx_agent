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

_TBD_

## Development

_TBD_
