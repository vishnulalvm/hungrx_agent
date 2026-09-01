# workflows/collector_workflow/

LangGraph state machine that takes a restaurant from "we know its name"
to "we have published, verified menu/nutrition data." Note the package is
`workflows/`, not `langgraph/` — see the root `CLAUDE.md` for why that
matters (naming collision with the real `langgraph` library).

## Files

- `state.py` — `CollectorState(TypedDict, total=False)`: the shared state
  every node reads/writes. Key fields: `agent_run_id`, `restaurant`,
  `source_url`, `source`, `source_snapshot`, `extraction_result`,
  `structured_json`, `validation_result`, `proposed_changes` (uses a
  `_keep_last` reducer — replaced, not accumulated), `errors` (uses
  `operator.add` — accumulated across nodes), `human_approval_status`.
- `graph.py` — `build_graph(session: AsyncSession, provider:
  EntityResolutionProvider | None = None) -> CompiledStateGraph`. Builds
  a fresh graph scoped to one DB session/provider — **not** a
  process-wide singleton, because Source Authority needs a live session.
  Defines the linear pipeline with a conditional branch after
  `human_review` (only `ProposedChangeStatus.APPROVED` routes to
  `publish`; anything else, including an unset status, ends the run).
- `nodes/` — one file per pipeline stage.
- `tools/` — currently empty; reserved for LangChain tool wrappers a node
  might need (e.g. an extraction tool).

## Pipeline stages

1. **source_authority** (`nodes/source_authority.py`) — **fully
   implemented**, not a placeholder. `build_source_authority_node(session,
   provider)` closes over a DB session/provider and returns the node
   function (nodes have a fixed `(state) -> partial_update` signature, so
   dependencies have to be injected via this factory pattern rather than
   passed as extra args). Behavior:
   - Builds an `EntityResolutionQuery` from `state["restaurant"]` (name +
     first location's city/state/country/phone). Missing `restaurant` on
     state fails closed with a logged error rather than raising.
   - Creates an `AgentRun` row (`RUNNING`) at the start of every
     invocation.
   - Calls `SourceAuthorityService.resolve_official_website` (see
     `infrastructure/source_authority/README.md` for the confidence
     tiers).
   - Only a `VERIFIED` result sets `source_url`/`source` on the returned
     state update — every other status omits those keys entirely. This
     is the "never hallucinate URLs" guarantee; it's structural, not a
     prompt instruction.
   - On any non-`VERIFIED` result: logs a warning, writes an `AuditLog`
     row (`AGENT_RUN_TRIGGER`, entity type `AGENT_RUN`, metadata has
     status/reason/rejected_candidates), marks the `AgentRun` `FAILED`
     with a truncated error message, and appends a structured error to
     `state["errors"]`.
   - Leaves the `AgentRun` `RUNNING` (not `SUCCEEDED`) on success, since
     Source Authority is only stage 1 of 6 — finalizing the run belongs
     to whatever completes the whole pipeline (not yet built).
2. **extraction** (`nodes/extraction.py`) — placeholder. Will run AI
   extraction against the source snapshot to produce `extraction_result`
   / `structured_json`.
3. **multimodal_translation** (`nodes/multimodal_translation.py`) —
   placeholder. Intended for image/PDF-heavy pages where text extraction
   alone isn't enough.
4. **deterministic_validation** (`nodes/deterministic_validation.py`) —
   placeholder. Will validate `structured_json` against `core/schemas`
   (the `extra="forbid"` schemas) and produce `validation_result`.
5. **human_review** (`nodes/human_review.py`) — placeholder. Will surface
   `proposed_changes` for a human to approve/reject, setting
   `human_approval_status`. The conditional routing in `graph.py` already
   depends on this field even though the node itself doesn't set it yet.
6. **publish** (`nodes/publish.py`) — placeholder. Only reachable when
   `human_approval_status == ProposedChangeStatus.APPROVED`.

Every placeholder node currently returns
`{"errors": [{"node": "<name>", "message": "... is a placeholder — not yet implemented"}]}`
so a full end-to-end run surfaces exactly which stages are unfinished
rather than silently doing nothing.

## Testing

- `tests/unit/test_source_authority_node.py` — the real node's behavior
  (identification, aggregator rejection, persistence, AgentRun
  lifecycle, audit logging, never-hallucinate guarantee). Uses a local
  `FakeProvider(EntityResolutionProvider)` test double, not mocks.
- `tests/unit/test_collector_graph.py` — graph topology (nodes/edges
  present, conditional routing) and an end-to-end `ainvoke` smoke test
  confirming every placeholder reports its error and the run terminates
  cleanly.

Both use the real Postgres-backed `db_session` fixture from
`tests/conftest.py` — `build_graph`/`build_source_authority_node` always
need a real `AsyncSession`, there's no in-memory mode.
