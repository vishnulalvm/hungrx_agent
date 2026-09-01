# apps/worker/

Background job worker, intended to be Redis-backed (via
`infrastructure/queue/`) and to run LangGraph workflow invocations
(collector/reviewer) outside the request/response cycle.

**Current state: placeholder only.** `app/main.py` is a bare `while
True: time.sleep(60)` loop just to keep the container alive under
`restart: unless-stopped` in `docker-compose.yml`. `app/jobs/` and
`app/tasks/` are empty scaffolding — no real job processing exists yet.

When this gets built out, the natural shape is: pull a job off the Redis
queue (`infrastructure/queue/base.py` defines the adapter interface),
open a DB session, call `workflows.collector_workflow.graph.build_graph(session,
provider).ainvoke(initial_state)`, and let the graph's own `AgentRun`/
`AuditLog` writes handle observability — the worker shouldn't need its
own separate logging of what the graph already records.
