# syntax=docker/dockerfile:1
#
# FastAPI API service. Built from the monorepo root so it can install the
# shared root-level packages (core, database, infrastructure, langgraph)
# alongside apps/api itself.
#
# Build with:
#   docker build -f apps/api/Dockerfile -t hungrx-api .
# Targets: "dev" (hot reload) and "prod" (default, optimized runtime).

# ---------------------------------------------------------------------------
# Base — shared OS deps + uv, no application code yet
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

WORKDIR /app

# ---------------------------------------------------------------------------
# Deps — resolved/installed separately so this layer caches across app-code
# changes; invalidated only when pyproject.toml / uv.lock change.
# ---------------------------------------------------------------------------
FROM base AS deps

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---------------------------------------------------------------------------
# dev — full source bind-mounted at runtime by docker-compose; hot reload
# via `uvicorn --reload`
# ---------------------------------------------------------------------------
FROM deps AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY . .

EXPOSE 8000

CMD ["uvicorn", "apps.api.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", \
     "--reload-dir", "/app/apps/api", "--reload-dir", "/app/core", \
     "--reload-dir", "/app/database", "--reload-dir", "/app/infrastructure", \
     "--reload-dir", "/app/langgraph"]

# ---------------------------------------------------------------------------
# prod — copies source in (no bind mount), no dev tooling, runs as non-root
# ---------------------------------------------------------------------------
FROM deps AS prod

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --shell /bin/bash --create-home app

COPY --chown=app:app apps/api ./apps/api
COPY --chown=app:app core ./core
COPY --chown=app:app database ./database
COPY --chown=app:app infrastructure ./infrastructure
COPY --chown=app:app langgraph ./langgraph
COPY --chown=app:app pyproject.toml uv.lock ./

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "apps.api.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
