# workflows/reviewer_workflow/

LangGraph state machine that checks an already-published restaurant's
verified source for drift and, when something has actually changed,
proposes an update for a human to approve — the counterpart to
`workflows/collector_workflow/` (which takes a restaurant from "we know
its name" to "first published"). Same package-naming rule applies here:
`workflows/`, not `langgraph/` — see the root `CLAUDE.md`.

## Files

- `state.py` — `ReviewerState(TypedDict, total=False)`: the shared state
  every node reads/writes. Key fields: `agent_run_id`, `restaurant_id`,
  `restaurant` (the currently **published** restaurant, loaded
  read-only), `source`, `previous_content_hash`/`current_content_hash`/
  `hash_changed`/`polled_snapshot` (Temporal Hash Polling output),
  `reextraction_snapshots`/`reextracted_structured_json` (Targeted
  Re-Extraction output), `delta` (JSON Delta Generation output),
  `validation_result`/`validated_structured_json` (Delta Validation
  output), `proposed_change_id`/`human_approval_status` (Human Final
  Sync), `published_restaurant_id`, `errors`.
- `graph.py` — `build_graph(session: AsyncSession, *, storage:
  StorageAdapter, ai_provider: AIProvider, checkpointer:
  BaseCheckpointSaver, settings: Settings | None = None) ->
  CompiledStateGraph`. Same required-dependency shape as the collector
  workflow's `build_graph` (no safe default for `storage`/`ai_provider`/
  `checkpointer` — see that module's docstring for why). Defines the
  pipeline with a conditional branch after `temporal_hash_polling` (only
  `hash_changed is True` continues to `targeted_reextraction`; anything
  else — unchanged, or a fail-closed error leaving it unset — ends the
  run there) and a second conditional branch after `human_final_sync`
  (only `ProposedChangeStatus.APPROVED` routes to `publish`, mirroring
  the collector workflow's `_route_after_human_review` exactly).
- `nodes/` — one file per pipeline stage, plus `publish.py` (the
  reviewer workflow's own terminal node — see below for why it isn't
  shared with the collector workflow's).
- `tools/` — currently empty; reserved for LangChain tool wrappers a
  node might need, same placeholder role as the collector workflow's.

## Pipeline stages

1. **temporal_hash_polling** (`nodes/temporal_hash_polling.py`, Reviewer
   Workflow Agent 6: Change Detection) — the workflow's early-out gate,
   and the only node optimized specifically for minimum compute/zero LLM
   usage: a single lightweight HTTP GET is the entire cost of a poll
   that finds nothing changed. Given `state["restaurant"]` (required;
   `state["source"]` is an optional caller-supplied hint, never trusted
   as-is), `build_temporal_hash_polling_node(session, storage, settings,
   *, fetcher_factory=None)`:
   - **Loads the active source URL** itself, from the database, via
     `SourceRepository.get_verified_website_for_restaurant` — never
     trusts `state["source"]` blindly, since a caller-supplied Source
     could be stale (a different website verified since). Falls back to
     a caller-supplied `state["source"]` only if it's genuinely
     `is_verified_domain=True` and the database lookup found nothing;
     reports an error and stops (no `agent_run_id` even created) if
     neither yields a usable source.
   - Creates an `AgentRun` (`workflow_type=REVIEWER`) once a source is
     resolved.
   - Looks up the most recent `SourceSnapshotRow` for the source
     (`database/repositories/source_snapshot_repository.py` — the
     durable persistence layer this workflow needed and the collector
     workflow didn't, since the collector's `SourceSnapshot`s only ever
     lived on in-memory LangGraph state for one run; the reviewer
     workflow needs "what was the hash last time" to survive across
     separate runs, potentially days apart).
   - Fetches the source's root page fresh (HTTP-only, via the injectable
     `RootPageFetcher` seam — production uses
     `CrawlerServiceRootPageFetcher`, tests fake it, same pattern as the
     collector workflow's `PageFetcher`) and hashes it (SHA-256, the same
     `infrastructure.crawler.hashing` the collector workflow uses, so
     hashes are directly comparable across both workflows).
   - No prior snapshot at all (first-ever poll) is always treated as
     `hash_changed = True`. Otherwise hash equality decides it.
   - Persists the freshly fetched snapshot **regardless of outcome** —
     "what did we last see" always reflects the most recent poll, not
     just the most recent change.
   - **Records agent run metrics** via `AgentRunRepository.update_metrics`
     onto `AgentRun.metrics` (a JSONB column, merged not overwritten
     across calls within a run): `fetch_duration_ms`,
     `content_length_bytes`, `hash_changed`, and `outcome`
     (`"changed"`/`"unchanged"`/`"fetch_failed"`) — durable and
     queryable, distinct from the audit log's discrete-event record
     (also still written, for the human-readable audit trail).
   - An unchanged result is not an error: it's logged as an
     audit-visible "nothing to do" outcome and the `AgentRun` still
     completes `SUCCEEDED`. Graph routing (`_route_after_hash_polling`
     in `graph.py`), not an error, is what stops the run here.
2. **targeted_reextraction** (`nodes/targeted_reextraction.py`) — only
   reached when `hash_changed is True`. Re-runs the same capture +
   AI-structuring path the collector workflow's `extraction`/
   `multimodal_translation` nodes use — same deterministic link
   discovery, same strict-structured-output boundary
   (`AIProvider.generate_structured(..., response_model=
   ExtractionOutput)`), same "never write to a restaurant/menu/dish
   repository directly" rule — reusing
   `workflows.collector_workflow.nodes.extraction`'s `PageFetcher`/
   `CrawlerServicePageFetcher` seam directly rather than a duplicate one.
   The one real difference: the AI's `ExtractionOutput` is mapped onto
   the **currently published** `Restaurant` (`state["restaurant"]`)
   instead of a blank one, so anything the fresh crawl didn't re-report
   falls back to what's already live rather than to empty/default
   values.
3. **json_delta_generation** (`nodes/json_delta_generation.py`) — pure,
   no I/O. `compute_delta(current, reextracted)` uses DeepDiff (already
   a project dependency) over both restaurants'
   `model_dump(mode="json")` dicts, with a custom
   `iterable_compare_func` that matches list items (menus/categories/
   dishes) by their stable `id` rather than list position, and produces
   `core.schemas.diff.JSONDelta`/`FieldDelta` — the first place in the
   codebase those schemas actually get produced (every other reference
   to them, `core.schemas.proposed_change.ProposedChange`, predates the
   workflow that fills them in). `id`/`created_at`/`updated_at` are
   filtered out at the top level (construction artifacts, not real
   content changes). An empty delta is still a valid outcome — it means
   the underlying content didn't meaningfully change even though the raw
   page bytes hashed differently (a timestamp, an ad slot) — and the run
   still continues through Delta Validation and Human Final Sync rather
   than short-circuiting a second time; only the hash check is allowed
   to stop the run early.
4. **delta_validation** (`nodes/delta_validation.py`) — runs the exact
   same deterministic validation engine (`core.validation.validate`) the
   collector workflow's `deterministic_validation` node uses, against
   the full re-extracted restaurant (a field-level diff alone can't
   independently check something like Atwater consistency or duplicate-
   dish detection). Same "never silently change AI-generated data"
   guarantee — only ever replaces data via
   `core.validation.safe_corrections` (pure formatting), everything else
   becomes a reported issue.
5. **human_final_sync** (`nodes/human_final_sync.py`) — the graph's
   human-in-the-loop pause point, reusing the exact same `ProposedChange`/
   interrupt/resume mechanics as the collector workflow's `human_review`
   node (idempotent creation via `ProposedChangeRepository.
   get_by_thread_id`, `interrupt(review_task)`, decision mapped onto
   `human_approval_status`) rather than a separate implementation — the
   admin review-queue infrastructure (`apps/api/app/services/
   review_service.py`, the `/api/v1/admin/reviews` endpoints) doesn't
   care which workflow produced a given `ProposedChange`, only that its
   `thread_id` can resume the paused run that created it. The interrupt
   payload additionally carries `delta` (the field-level diff) alongside
   the usual `is_valid`/`issue_count`, so a reviewer sees what changed,
   not just the final state.
6. **publish** (`nodes/publish.py`) — the reviewer workflow's own
   terminal node, not shared with the collector workflow's
   `workflows.collector_workflow.nodes.publish`. Same core guarantees
   (re-checks `human_approval_status == APPROVED` itself, re-validates
   immediately before commit, refuses on any `ERROR`-severity issue) but
   a different write shape: a reviewer-workflow publish always updates
   an **already-published** restaurant (the collector workflow's
   `publish_node` refuses to republish an existing `entity_id` — that
   guard is specific to the collector inserting a brand-new restaurant),
   so this node deletes the prior production tree
   (`RestaurantRepository`'s ORM cascade handles
   locations/menus/categories/dishes) and re-inserts the validated one
   with the same restaurant id, inside the same uncommitted transaction
   as everything else — a failure partway through still leaves nothing
   written once the caller rolls back. Refuses outright if no existing
   production row exists for the entity (this workflow never creates a
   restaurant, only updates one).

## Testing

- `tests/unit/test_reviewer_graph.py` — graph topology (nodes/edges,
  both conditional branches present) and the routing functions
  (`_route_after_hash_polling`/`_route_after_human_final_sync`) tested
  directly, plus an end-to-end proof that an unchanged hash stops the
  run at `temporal_hash_polling` without ever constructing the
  downstream nodes' dependencies.
- `tests/unit/test_temporal_hash_polling_node.py` — first-ever-poll
  behavior, matching/differing hash outcomes, snapshot persistence
  regardless of outcome, fetch-failure handling, loading the active
  source from the database (ignoring a stale state-provided one; falling
  back to a verified state-provided one when the DB lookup finds
  nothing), `AgentRun.metrics` recording for the changed/unchanged/
  fetch-failed outcomes, and the changed/unchanged pair named exactly
  for what the task asked — using a fake `RootPageFetcher` (no real
  network).
- `tests/unit/test_targeted_reextraction_node.py` — source-material-only
  prompts, mapping onto the currently published restaurant (untouched
  fields keep their live value), fail-closed paths, using a fake
  `PageFetcher`/`AIProvider` (no real network/browser/OpenAI call).
- `tests/unit/test_json_delta_generation.py` — pure `compute_delta`
  tests: empty delta for identical restaurants, changed/added/removed
  detection, id/timestamp noise filtering, and the
  `iterable_compare_func` regression case (an unrelated sibling must not
  appear "changed" just because a list position shifted).
- `tests/unit/test_delta_validation_node.py` — pass-through/correction
  behavior and fail-closed handling; the validation rules themselves are
  already exhaustively covered in `tests/unit/test_validation_engine.py`.
- `tests/unit/test_human_final_sync_node.py` — pause/resume against a
  real Postgres-backed checkpointer (mirroring
  `tests/unit/test_human_review_node.py`'s structure), plus the
  reviewer-specific case of approving an update to an already-published
  restaurant.
- `tests/unit/test_reviewer_publish_node.py` — updates an existing
  production row, refuses when no production row exists yet, refuses on
  re-validation failure, refuses unapproved data.

All of these use the real Postgres-backed `db_session` fixture from
`tests/conftest.py`; anything touching `human_final_sync`'s actual
pause/resume uses the real `checkpointer` fixture, same reasoning as the
collector workflow's equivalent tests.
