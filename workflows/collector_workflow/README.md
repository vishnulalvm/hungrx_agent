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
  EntityResolutionProvider | None = None, *, storage: StorageAdapter,
  ai_provider: AIProvider, settings: Settings | None = None) ->
  CompiledStateGraph`. Builds a fresh graph scoped to one DB
  session/provider/storage/AI-provider — **not** a process-wide
  singleton, because Source Authority needs a live session, Extraction
  needs live storage, and Multimodal Translation needs a live AI
  provider. Defines the linear pipeline with a conditional branch after
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
2. **extraction** (`nodes/extraction.py`) — **fully implemented**, not a
   placeholder — but deliberately does not interpret content. Given
   `state["source"]`/`state["source_url"]` from Source Authority,
   `build_extraction_node(session, storage, settings)`:
   - Fetches the source page (HTML or PDF, via `CrawlerService`).
   - For HTML pages, runs deterministic (keyword-based, not AI) link
     discovery (`infrastructure/crawler/page_discovery.py`) to find
     menu/nutrition-relevant linked pages, and fetches each one too.
   - Falls back to a Playwright screenshot capture when the root page's
     HTML looks suspiciously thin (likely a client-side-rendered shell)
     — the one case browser automation is used, per the crawler's
     httpx-by-default rule.
   - Persists every fetch as a `SourceSnapshot` and returns them on state
     as `source_snapshot` (first/primary) and `source_snapshots` (full
     list) — never raw content, only references.
   - Fails closed (logged error, no snapshot) if `source`/`source_url`
     are missing, or if any fetch raises; on failure with an
     `agent_run_id` present, writes an `AGENT_RUN_TRIGGER` audit row and
     marks the `AgentRun` `FAILED`, same pattern as Source Authority.
   - Network/browser access goes through an injectable `PageFetcher`
     seam (`CrawlerServicePageFetcher` in production); this is what lets
     `tests/unit/test_extraction_node.py` exercise the node's own
     discovery/persistence/error-handling logic with a fake, no real
     network or browser required.
   - Does **not** parse menu items, prices, or nutrition values out of
     anything it captures — that's `structured_json`/AI interpretation,
     which belongs to later nodes.
3. **multimodal_translation** (`nodes/multimodal_translation.py`) —
   **fully implemented**, not a placeholder. Given
   `state["restaurant"]`/`state["source_snapshots"]` from Extraction,
   `build_multimodal_translation_node(session, storage, ai_provider)`:
   - Reads back **only** the HTML snapshots' text (via `StorageAdapter`)
     and sends **only** that collected source material to the model —
     no restaurant name, address, or any other database context is ever
     included in the prompt (see
     `tests/unit/test_multimodal_translation_node.py`'s
     `TestSendsOnlyCollectedSourceMaterial`). PDF/screenshot snapshots
     are skipped for now (no PDF-text pipeline yet) but remain on state
     as references for a future node.
   - Calls `AIProvider.generate_structured(..., response_model=
     ExtractionOutput)` (`core/schemas/extraction_output.py`) — a
     dedicated, strict (`extra="forbid"`), AI-output-only schema
     distinct from the real `Restaurant`/`Menu`/`Dish` schemas: no `id`
     fields the model could collide with real primary keys, and every
     extracted dish/profile carries `confidence` and
     `source_snapshot_ids`. `OpenAIProvider` enforces this at the API
     level (`response_format=<model>`), not by prompt instruction —
     "do not allow free-form output" is a real constraint, not a
     convention.
   - Maps the AI's `ExtractionOutput` onto the caller-known `Restaurant`
     in Python (`_map_to_restaurant`) — real `uuid`s are assigned here,
     never by the model; identity/location fields the model was never
     asked for are left untouched. The merged `Restaurant` becomes
     `structured_json`; the raw `ExtractionOutput` (with confidence and
     source references intact) becomes `extraction_result`.
   - Never touches a restaurant/menu/dish repository or table — the only
     database writes this node can make are `AgentRun`/`AuditLog`
     (`AuditAction.AI_EXTRACTION`), the same audit-only pattern as
     Source Authority/Extraction. It returns a state update; nothing
     about "modifying the database directly" is even reachable from
     this node's imports.
   - Fails closed (logged error, no `structured_json`) if
     `restaurant`/`source_snapshots` are missing, if no snapshot has
     text-readable content, or if the AI call raises
     `AIProviderError` — never falls back to a partial/guessed result.
   - See `infrastructure/ai/README.md` for the `AIProvider` interface
     itself and how to add a different model backend.
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

- `tests/unit/test_extraction_node.py` — Extraction's own behavior
  (HTML flow with link discovery, PDF flow with no discovery, thin-HTML
  screenshot fallback, snapshot persistence references, error handling)
  using a `FakePageFetcher` test double.
- `tests/unit/test_page_discovery.py` — pure unit tests for the
  deterministic menu/nutrition link-matching logic, no network/DB at
  all.
- `tests/unit/test_multimodal_translation_node.py` — Multimodal
  Translation's own behavior (source-material-only prompts, strict
  structured output, domain-schema mapping, source references,
  confidence metadata, database-isolation, fail-closed paths, audit
  logging) using a `FakeAIProvider`/`FakeStorageAdapter` — no real
  OpenAI call.
- `tests/unit/test_openai_provider.py` — `OpenAIProvider` itself, with
  `AsyncOpenAI.chat.completions.parse` mocked out (`unittest.mock.AsyncMock`)
  — verifies the request shape (`response_format`, messages), refusal
  handling, and transport-error wrapping into `AIProviderError`. No real
  network call.

All of these use the real Postgres-backed `db_session` fixture from
`tests/conftest.py` — `build_graph`/`build_source_authority_node`/
`build_extraction_node`/`build_multimodal_translation_node` always need a
real `AsyncSession`, there's no in-memory mode. `build_graph` also now
requires `storage` (`StorageAdapter`) and `ai_provider` (`AIProvider`)
arguments (e.g. `LocalStorageAdapter(tmp_path)` and a fake `AIProvider`
in tests) since Extraction persists crawl captures and Multimodal
Translation calls the AI provider through them.
