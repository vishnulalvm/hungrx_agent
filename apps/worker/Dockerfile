# syntax=docker/dockerfile:1
#
# Background worker service (RQ over Redis). Built from the monorepo root so
# it can install the shared root-level packages (core, database,
# infrastructure, langgraph) alongside apps/worker itself.
#
# Build with:
#   docker build -f apps/worker/Dockerfile -t hungrx-worker .
# Targets: "dev" (auto-restart via watchfiles) and "prod" (default).

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

# Playwright's browser deps live here too since the worker is what actually
# runs crawl jobs; the browsers themselves are installed after uv sync below.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

WORKDIR /app

# ---------------------------------------------------------------------------
# Deps
# ---------------------------------------------------------------------------
FROM base AS deps

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

RUN /opt/venv/bin/playwright install --with-deps chromium

# ---------------------------------------------------------------------------
# dev — full source bind-mounted at runtime; watchfiles restarts the worker
# process on code changes since RQ workers have no built-in reload flag
# ---------------------------------------------------------------------------
FROM deps AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY . .

CMD ["watchfiles", "--filter", "python", \
     "python -m apps.worker.app.main", \
     "/app/apps/worker", "/app/core", "/app/database", "/app/infrastructure", "/app/langgraph"]

# ---------------------------------------------------------------------------
# prod — copies source in, no dev tooling, runs as non-root
# ---------------------------------------------------------------------------
FROM deps AS prod

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --shell /bin/bash --create-home app

COPY --chown=app:app apps/worker ./apps/worker
COPY --chown=app:app core ./core
COPY --chown=app:app database ./database
COPY --chown=app:app infrastructure ./infrastructure
COPY --chown=app:app langgraph ./langgraph
COPY --chown=app:app pyproject.toml uv.lock ./

RUN mkdir -p /home/app/.cache/ms-playwright \
    && cp -r /root/.cache/ms-playwright/* /home/app/.cache/ms-playwright/ \
    && chown -R app:app /home/app/.cache

ENV PLAYWRIGHT_BROWSERS_PATH=/home/app/.cache/ms-playwright

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import redis, os; redis.from_url(os.environ['REDIS_URL']).ping()" || exit 1

CMD ["python", "-m", "apps.worker.app.main"]
