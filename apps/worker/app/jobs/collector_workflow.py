"""Job: run the collector LangGraph workflow for a restaurant.

Invokes workflows.collector_workflow.graph.build_graph from START with
just a Restaurant identity on state — the graph's own source_authority
node re-resolves/re-verifies the official site and creates its own
AgentRun (see workflows/collector_workflow/nodes/source_authority.py),
and extraction independently captures the menu/nutrition pages it needs;
this job does not pre-seed source/source_snapshot state, since nothing
downstream trusts a job-supplied value over what the graph derives
itself. The run pauses at human_review (LangGraph interrupt()) exactly
as it would from any other caller — this job's job is done once
`ainvoke` returns (paused or finished), not once a human has reviewed it.

Idempotency / no duplicate work: locked per restaurant_id — the
collector workflow's source_authority node always creates a brand-new
AgentRun, so a second run for the same restaurant starting before the
first one reaches human_review would be genuinely duplicate work (two
concurrent crawls/AI extractions of the same site), not just a duplicate
enqueue.
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

JOB_TYPE = "collector_workflow"


async def _run(*, restaurant_id: str, restaurant_name: str, city, state, country, phone) -> dict[str, Any]:
    import uuid

    from core.schemas.restaurant import Restaurant, RestaurantLocation
    from infrastructure.checkpointer import get_checkpointer
    from workflows.collector_workflow.dependencies import default_ai_provider, default_storage_adapter
    from workflows.collector_workflow.graph import build_graph

    settings = get_settings()
    session_factory = get_sessionmaker()

    locations = []
    if city and country:
        locations.append(RestaurantLocation(address_line1="Unknown", city=city, state=state, country=country))
    restaurant = Restaurant(id=uuid.UUID(restaurant_id), name=restaurant_name, locations=locations)

    async with session_factory() as session:
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
        "agent_run_id": result.get("agent_run_id"),
        "published_restaurant_id": result.get("published_restaurant_id"),
        "errors": result.get("errors", []),
    }


def run_collector_workflow(
    *,
    restaurant_id: str,
    restaurant_name: str = "",
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    phone: str | None = None,
    source_id: str | None = None,
    source_snapshot_id: str | None = None,
) -> dict[str, Any]:
    """RQ entry point. `source_id`/`source_snapshot_id` (from a preceding
    source_crawl job) are accepted for correlation/logging only — see
    module docstring for why the graph re-derives its own source state
    rather than trusting these."""
    configure_logging(get_settings())
    job = get_current_job()
    job_id = job.id if job else "sync"

    log_started(
        job_id=job_id,
        job_type=JOB_TYPE,
        restaurant_id=restaurant_id,
        source_id=source_id,
        source_snapshot_id=source_snapshot_id,
    )

    try:
        with RestaurantJobLock(get_redis_connection(), restaurant_id=restaurant_id, job_type=JOB_TYPE):
            result = run_async(
                _run,
                restaurant_id=restaurant_id,
                restaurant_name=restaurant_name,
                city=city,
                state=state,
                country=country,
                phone=phone,
            )
    except JobAlreadyRunningError as exc:
        log_skipped(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, reason=str(exc))
        raise
    except Exception as exc:
        log_failed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, error=str(exc))
        raise

    log_completed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, **result)
    return result
