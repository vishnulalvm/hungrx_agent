"""RQ job functions are plain sync callables (RQ has no native asyncio
support), but effectively all application code they need to call
(SQLAlchemy AsyncSession, the LangGraph workflow graphs, the
checkpointer) is async. `run_async(coro_fn, *args, **kwargs)` is the one
bridge point every job module uses instead of each reimplementing its
own `asyncio.run(...)` wrapper.

Deliberately just `asyncio.run` — each RQ job execution is a fresh call
in the worker process with no already-running event loop to conflict
with (RQ workers execute jobs synchronously, one at a time, in a forked
or in-process worker, not inside an asyncio loop themselves), so there is
no event-loop-reuse complexity to manage here.
"""

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


def run_async(coro_fn: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
    return asyncio.run(coro_fn(*args, **kwargs))
