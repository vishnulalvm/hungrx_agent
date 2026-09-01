# workflows/collector_workflow/

LangGraph state machine that takes a restaurant from "we know its name"
to "we have published, verified menu/nutrition data." Note the package is
`workflows/`, not `langgraph/` — see the root `CLAUDE.md` for why that
matters (naming collision with the real `langgraph` library).

## Files

- `state.py` — `CollectorState(TypedDict, total=False)`: the shared state
  every node reads/writes. Key fields: `agent_run_id` (also doubles as
  the LangGraph checkpoint `thread_id` — see human_review below),
  `restaurant`, `source_url`, `source`, `source_snapshot`,
  `extraction_result`, `structured_json`, `validation_result`,
  `proposed_change_id`, `proposed_changes` (uses a `_keep_last` reducer —
  replaced, not accumulated), `human_approval_status`,
  `published_restaurant_id`, `errors` (uses `operator.add` — accumulated
  across nodes).
- `graph.py` — `build_graph(session: AsyncSession, provider:
  EntityResolutionProvider | None = None, *, storage: StorageAdapter,
  ai_provider: AIProvider, checkpointer: BaseCheckpointSaver, settings:
  Settings | None = None) -> CompiledStateGraph`. Builds a fresh graph
  scoped to one DB session/provider/storage/AI-provider/checkpointer —
  **not** a process-wide singleton. `storage`, `ai_provider`, and
  `checkpointer` all have no safe default and must be passed explicitly
  (see the docstring for why). Defines the linear pipeline with a
  conditional branch after `human_review` (only
  `ProposedChangeStatus.APPROVED` routes to `publish`; anything else,
  including an unset status — which includes the paused/interrupted
  case — ends the run).
- `dependencies.py` — `default_storage_adapter`/`default_ai_provider`:
  the process-default `storage`/`ai_provider` instances used by callers
  (like `ReviewService`) that need a compiled graph without already
  having their own crawl-job-scoped instances. `default_ai_provider`
  returns a `_LazyOpenAIProvider` that defers API-key validation until
  actually called, since resuming a paused `human_review` never
  re-reaches `multimodal_translation`.
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
   **fully implemented**, not a placeholder, and the one collector node
   with no AI/network/storage dependency at all —
   `build_deterministic_validation_node(session)` only needs a DB session
   for `AgentRun`/`AuditLog` bookkeeping. Given `state["structured_json"]`
   from Multimodal Translation:
   - Runs `core.validation.validate(structured_json)` — the actual rule
     engine (schema conformance, nutrition/Atwater checks, allergen
     taxonomy cross-checks, price validation, required-field checks,
     duplicate detection, impossible-value detection) lives in
     `core/validation/` (see `core/validation/README.md`), completely
     independent of LangGraph — deterministic, no model call, unit
     tested on its own.
   - Sets `validation_result` (`is_valid`, `issues` — errors and
     warnings both, distinguished by `severity`) on state.
   - Only replaces `state["structured_json"]` when the validator actually
     applied a safe correction (`ValidationOutcome.corrected_fields`
     non-empty) — corrections are restricted to pure formatting
     (whitespace collapse, currency casing, exact-duplicate-ingredient
     removal); numeric/monetary/AI-inferred values (calories, price,
     allergens, names) are **never** rewritten, only ever reported as an
     issue. See `core/validation/README.md`'s "Never silently changes
     AI-generated data" section.
   - Fails closed (logged error) if `structured_json` is missing.
   - When the result is invalid, or a correction was applied, with an
     `agent_run_id` present: writes an `AGENT_RUN_TRIGGER` audit row
     (metadata includes `is_valid`, error/warning counts, and which
     fields were corrected) and marks the `AgentRun` `FAILED` on
     invalid results — same audit-on-failure pattern as the earlier
     nodes. A valid result with zero corrections writes nothing (no
     audit noise for the common "everything's fine" case).
5. **human_review** (`nodes/human_review.py`) — **fully implemented**,
   the graph's human-in-the-loop pause point. Given
   `state["structured_json"]`/`state["validation_result"]`/
   `state["agent_run_id"]` from Deterministic Validation,
   `build_human_review_node(session)`:
   - Looks up an existing `ProposedChange` by `thread_id` (==
     `agent_run_id`); creates one (status `PENDING`, audited as
     `PROPOSED_CHANGE_CREATE`) only if none exists yet. This lookup — not
     anything carried on `state` — is what makes creation idempotent
     across LangGraph's node-replay-on-resume behavior; see the module
     docstring for the exact bug a naive state-based check would hit.
   - Calls `interrupt(review_task)`. On first entry this suspends the
     entire graph run — the surrounding `graph.ainvoke`/`astream` call
     returns immediately with an `__interrupt__` entry instead of a
     completed result, and nothing past this point in the pipeline
     executes. On a resumed invocation (via
     `graph.ainvoke(Command(resume=decision), config)` against the same
     `thread_id`), `interrupt()` returns `decision` directly instead of
     pausing again.
   - Maps `decision["action"]` (`"approve"` / `"reject"` /
     `"edit_then_approve"`) onto `human_approval_status`, and — for
     `edit_then_approve` — replaces `structured_json` with
     `decision["edited_structured_json"]` before returning.
   - Does **not** itself update the `ProposedChange` row's status or
     write an `Approval`/decision-audit row — that happens in
     `apps/api/app/services/review_service.py`, in the same
     request/transaction as the admin's HTTP call, so the API response
     reflects the decision synchronously rather than racing the resumed
     graph.
6. **publish** (`nodes/publish.py`) — **fully implemented**, the only
   code in the repo permitted to write to the production
   `restaurants`/`restaurant_locations`/`menus`/`menu_categories`/`dishes`
   tables (`database/models/restaurant.py`, via
   `database/repositories/restaurant_repository.py`, which is imported
   nowhere else). `build_publish_node(session)`:
   - Re-checks `state["human_approval_status"] ==
     ProposedChangeStatus.APPROVED` itself — defense in depth against a
     graph-routing bug, even though topology already only routes here on
     APPROVED — and refuses to write (returns an error instead) if it
     somehow wasn't.
   - **Re-validates immediately before commit**: re-runs
     `core.validation.validate(structured_json)` (the same deterministic
     engine `deterministic_validation` uses) and refuses to publish on
     any `ERROR`-severity issue. Necessary because a reviewer's
     `edit_then_approve` can hand this node data that was never re-run
     through validation after being hand-edited — an approval is a
     decision about *some* version of the data, not a guarantee the
     final `structured_json` still passes.
   - **Refuses to republish** an `entity_id` that already has a
     `PUBLISHED` `ProposedChange` (`ProposedChangeRepository.
     get_published_for_entity`) — publish always represents a brand-new
     entity, never a silent overwrite of an existing production
     restaurant, so every prior `ProposedChange`/`Approval` for that
     entity is preserved as history rather than superseded.
   - Parses `state["structured_json"]` back into a `Restaurant` and calls
     `RestaurantRepository.persist_tree(...)`, which recursively inserts
     the restaurant, its locations, menus, the (possibly deeply nested)
     category tree, and every dish. Nothing in this node (or
     `persist_tree`) ever calls `commit()` — every write, including the
     `ProposedChange`/`AgentRun`/audit updates below, rides in the
     caller's own transaction, so any failure (re-validation, a DB
     constraint violation, anything) leaves the whole tree unwritten
     rather than partially committed — see
     `tests/unit/test_publish_node.py`'s `TestRollsBackOnFailure`.
   - Marks the `ProposedChange` `PUBLISHED`, writes a
     `PROPOSED_CHANGE_PUBLISH` audit row, and marks the `AgentRun`
     `SUCCEEDED` — this is the one node in the pipeline that finalizes an
     `AgentRun` as done, since it's the terminal success state of the
     whole pipeline. Any refusal path (unapproved, re-validation failure,
     republish attempt) instead marks the `AgentRun` `FAILED` with a
     descriptive error message, when an `agent_run_id` is present.
   - Returns `published_restaurant_id` on state — its mere presence on a
     finished run's result is itself evidence the data passed human
     review and was actually written.

The remaining human/business-logic side of a decision — creating the
`Approval` row, writing the `PROPOSED_CHANGE_APPROVE`/`_REJECT`/`_EDIT`
audit rows, and actually calling `graph.ainvoke(Command(resume=...), ...)`
— lives in `apps/api/app/services/review_service.py`, called from the
admin API's `/reviews` endpoints
(`apps/api/app/routers/v1/admin/router.py`):

- `GET /api/v1/admin/reviews` — pending queue (`REVIEW_READ`).
- `GET /api/v1/admin/reviews/{id}` — one review's full detail, including
  `structured_json`/`validation_result` (`REVIEW_READ`).
- `POST /api/v1/admin/reviews/{id}/approve` — resumes with `action:
  "approve"` (`REVIEW_WRITE`).
- `POST /api/v1/admin/reviews/{id}/reject` — resumes with `action:
  "reject"` (`REVIEW_WRITE`).
- `POST /api/v1/admin/reviews/{id}/edit-approve` — resumes with `action:
  "edit_then_approve"` plus the reviewer's edited `structured_json`
  (`REVIEW_WRITE`); writes a separate `PROPOSED_CHANGE_EDIT` audit row
  before the approval one, so "a human changed this data" and "a human
  approved it" are two distinct, individually auditable facts.

Every action re-checks the `ProposedChange` is still `PENDING` before
acting (a `409 Conflict` otherwise — see `ReviewService._require_pending`)
so a double-submit or acting on an already-decided review can't
double-resume the same paused graph run.

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
- `tests/unit/test_validation_engine.py` — extensive, pure (no DB/network)
  tests for `core.validation` itself: every rule category, safe
  corrections (with explicit "AI-generated data is never rewritten"
  assertions for calories/price/allergens), determinism, and
  input-immutability.
- `tests/unit/test_deterministic_validation_node.py` — the LangGraph
  node wrapper around `core.validation.validate`: `validation_result`
  shape, `structured_json` only replaced on an actual correction,
  fail-closed on missing input, AgentRun/AuditLog bookkeeping.
- `tests/unit/test_human_review_node.py` — Human Review's pause/resume
  behavior against a real Postgres-backed checkpointer (the
  `checkpointer` fixture, `tests/conftest.py` — an in-memory one
  wouldn't prove anything about durability): the graph actually pauses
  and returns `__interrupt__`, the interrupt payload shape, a
  `ProposedChange` is created on pause, resume routes correctly for all
  three decisions (approve/reject/edit_then_approve), and — the specific
  bug this design guards against — exactly one `ProposedChange`/one
  `PROPOSED_CHANGE_CREATE` audit row survives a full pause-then-resume
  cycle (`TestIdempotentRecordCreation`).
- `tests/unit/test_publish_node.py` — Publish's real production write
  (restaurant + full menu tree including dishes), ProposedChange/
  AgentRun/AuditLog bookkeeping, and — tested directly at the node level,
  not just via graph routing — that it refuses to write for every
  non-APPROVED status and for APPROVED-but-missing-data states. Also:
  `TestRevalidatesBeforeCommit` (a reviewer-edited payload that now fails
  deterministic validation is refused, even though `human_approval_status`
  is `APPROVED`, and marks the `AgentRun` `FAILED`),
  `TestPreventsRepublishing` (a second `APPROVED` `ProposedChange` for an
  already-published `entity_id` is refused, and the first publish's
  history is untouched), and `TestRollsBackOnFailure` (a real Postgres
  `IntegrityError` partway through the tree write leaves zero rows behind
  once the caller's transaction is rolled back).
- `tests/integration/test_human_in_the_loop.py` — the full HTTP-level
  cycle through the real FastAPI app and a real paused graph: pending
  list, review detail, approve → publish, reject → no publish,
  edit-then-approve → publishes the *edited* data, permission checks per
  endpoint, double-decision → `409`, and an explicit "a review nobody
  acts on writes nothing to production tables" check. Overrides
  `get_settings` (not just `get_db_session`) so `ReviewService`'s
  checkpointer/graph resolve against `TEST_DATABASE_URL` rather than the
  dev database — see the file's module docstring.

All of these use the real Postgres-backed `db_session` fixture from
`tests/conftest.py` — `build_graph`/`build_source_authority_node`/
`build_extraction_node`/`build_multimodal_translation_node`/
`build_deterministic_validation_node`/`build_human_review_node`/
`build_publish_node` always need a real `AsyncSession`, there's no
in-memory mode. `build_graph` also requires `storage` (`StorageAdapter`),
`ai_provider` (`AIProvider`), and `checkpointer` (`BaseCheckpointSaver`)
arguments (e.g. `LocalStorageAdapter(tmp_path)`, a fake `AIProvider`, and
either `InMemorySaver()` for topology-only tests or the real
`checkpointer` fixture for anything touching an actual pause/resume) —
Extraction persists crawl captures, Multimodal Translation calls the AI
provider, and Human Review/Publish need the checkpointer to actually
pause/resume durably; Deterministic Validation needs none of the three.
