"""LangGraph checkpointer wiring — durable, cross-process persistence of
paused graph runs. This is what makes the collector workflow's
human-in-the-loop pause real: the API request that pauses a run at
human_review (workflows/collector_workflow/nodes/human_review.py) is
never the same request that later resumes it after an admin's approve/
reject/edit decision, so the paused state has to survive on something
other than process memory. `langgraph.checkpoint.memory.MemorySaver` (the
only checkpointer bundled with the base `langgraph` package) doesn't
survive a process restart or a different worker process handling the
resume request; `AsyncPostgresSaver` (langgraph-checkpoint-postgres)
does, backed by the same Postgres database as everything else.

`database_url` is an asyncpg DSN (`postgresql+asyncpg://...`) for
SQLAlchemy; AsyncPostgresSaver uses psycopg directly and needs its own
plain `postgresql://...` DSN — `_to_psycopg_dsn` does that one
conversion, nothing more.

CollectorState carries our own Pydantic model instances (Restaurant,
Source, SourceSnapshot, ...), which the checkpointer has to serialize to
persist a paused run. LangGraph's default JsonPlusSerializer only
msgpack-serializes third-party/unregistered types when explicitly
allowlisted (a supply-chain safeguard against deserializing arbitrary
classes from checkpoint data written by something else) — since this
checkpoint database is written exclusively by our own graph, trusting
our own `core.schemas`/`database.models` types is safe; `allowed_msgpack_modules=True`
allowlists everything rather than enumerating every schema module
individually, which would need updating every time a new schema is added
to CollectorState.

Known cosmetic issue: even with the allowlist passed here, a
"Deserializing unregistered type ... This will be blocked in a future
version" warning can still print on resume — some internal LangGraph
deserialize path appears to use a different JsonPlusSerializer instance
than the one threaded through here. Deserialization itself is verified
correct (round-trips Restaurant/Source/SourceSnapshot faithfully; see
tests/unit/test_human_review_node.py's pause/resume tests), so this is
tracked as a warning to watch on future langgraph-checkpoint-postgres
upgrades, not a functional bug.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from core.config.settings import Settings


def _to_psycopg_dsn(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url[len("postgresql+asyncpg://") :]
    return database_url


@asynccontextmanager
async def get_checkpointer(settings: Settings) -> AsyncIterator[AsyncPostgresSaver]:
    """Yields a ready-to-use AsyncPostgresSaver, connected and with its
    own checkpoint tables ensured to exist (`setup()` is idempotent, so
    calling it on every checkout is safe, not just on first-ever use)."""
    dsn = _to_psycopg_dsn(settings.database_url)
    serde = JsonPlusSerializer(allowed_msgpack_modules=True)
    async with AsyncPostgresSaver.from_conn_string(dsn, serde=serde) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
