"""Job: run the reviewer LangGraph workflow for one already-published
restaurant — checks its source for drift, and if changed, carries the
run through re-extraction/diff/validation/human-review exactly as
workflows/reviewer_workflow/graph.py defines it.

Loads the restaurant's current published tree (RestaurantRepository.
get_full_tree) just to get an `id` onto ReviewerState.restaurant —
temporal_hash_polling only ever reads `.id` off it and reloads the
active Source itself from the database (see that node's docstring), so
nothing here needs to be fresher than "restaurant still exists."

Idempotency / no duplicate work: locked per restaurant_id, same shape as
collector_workflow's job. `ainvoke` returns as soon as the graph either
early-stops at the unchanged-hash gate, ends with nothing to review, or
pauses at human_final_sync's interrupt() — never blocks waiting on an
admin decision.
"""

from typing import Any

from rq import get_current_job

from apps.worker.app.jobs.logging import log_completed, log_failed, log_skipped, log_started
from core.config.logging import configure_logging
from core.config.settings import get_settings
from database.session import get_sessionmaker
from infrastructure.queue.async_bridge import run_async
from infrastructure.queue.lock import JobAlreadyRunningError, RestaurantJobLock
from infrastructure.queue.redis_connection import get_redis_connection

JOB_TYPE = "reviewer_workflow"


async def _run(*, restaurant_id: str) -> dict[str, Any]:
    import uuid

    from database.repositories.restaurant_repository import RestaurantRepository
    from infrastructure.checkpointer import get_checkpointer
    from workflows.collector_workflow.dependencies import default_ai_provider, default_storage_adapter
    from workflows.reviewer_workflow.graph import build_graph

    settings = get_settings()
    session_factory = get_sessionmaker()

    async with session_factory() as session:
        restaurant = await RestaurantRepository(session).get_full_tree(uuid.UUID(restaurant_id))
        if restaurant is None:
            return {"errors": [{"node": "reviewer_workflow_job", "message": f"no published restaurant {restaurant_id}"}]}

        async with get_checkpointer(settings) as checkpointer:
            graph = build_graph(
                session,
                storage=default_storage_adapter(settings),
                ai_provider=default_ai_provider(settings),
                checkpointer=checkpointer,
            )
            config = {"configurable": {"thread_id": str(uuid.uuid4())}}
            result = await graph.ainvoke({"restaurant": restaurant}, config)
        await session.commit()

    return {
        "hash_changed": result.get("hash_changed"),
        "published_restaurant_id": result.get("published_restaurant_id"),
        "errors": result.get("errors", []),
    }


def run_reviewer_workflow(*, restaurant_id: str) -> dict[str, Any]:
    """RQ entry point."""
    configure_logging(get_settings())
    job = get_current_job()
    job_id = job.id if job else "sync"

    log_started(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id)

    try:
        with RestaurantJobLock(get_redis_connection(), restaurant_id=restaurant_id, job_type=JOB_TYPE):
            result = run_async(_run, restaurant_id=restaurant_id)
    except JobAlreadyRunningError as exc:
        log_skipped(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, reason=str(exc))
        raise
    except Exception as exc:
        log_failed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, error=str(exc))
        raise

    log_completed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, **result)
    return result
