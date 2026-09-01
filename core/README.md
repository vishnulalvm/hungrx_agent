# core/

Shared, dependency-light building blocks used by every other module:
Pydantic schemas, configuration, and validation logic. Nothing here talks
to a database or the network.

## core/schemas/

Strict Pydantic v2 domain models — the source of truth for every shape
that flows through the system. **Every schema uses `extra="forbid"`**;
this is deliberate, not incidental, because AI extraction output gets
validated against these models and must never be able to smuggle in an
arbitrary key (this matters most for `nutrition.py`, where an
unconstrained key set would defeat the whole point of structured
nutrition data).

- `restaurant.py` — `Restaurant`, `RestaurantLocation` (address, ISO
  alpha-2 country, lat/lng bounds). Restaurant holds logo/cover/gallery
  URLs, locations, menus, metadata.
- `menu.py` — `Menu`, `MenuCategory` (recursively nestable via
  `children: list["MenuCategory"]` + `model_rebuild()`, blank-name
  rejected), `Dish` (fixed UI fields: name, description, image, nutrition,
  allergens, ingredients, quantity, price, category_id, review_state),
  `Allergen` enum, `Ingredient`, `ReviewState` enum.
- `nutrition.py` — `Nutrition`, `Macros`, `Micronutrients`. Fixed field
  sets only — this is the file most responsible for stopping hallucinated
  nutrition keys from AI extraction.
- `source.py` — `Source`, `SourceSnapshot`, `SourceType` enum,
  `SnapshotContentType` enum (HTML/PDF/SCREENSHOT).
- `source_authority.py` — types for the source-authority resolution flow:
  `ResolutionStatus`, `ConfidenceLevel`, `EntityResolutionQuery`,
  `EntityCandidate`, `SourceAuthorityResult`.
- `proposed_change.py` — `ProposedChange`, `Approval`,
  `ProposedChangeEntityType`, `ProposedChangeStatus`
  (PENDING/APPROVED/REJECTED/PUBLISHED).
- `agent_run.py` — Pydantic `AgentRun` schema, `AgentRunStatus`,
  `AgentWorkflowType`. Distinct from `database/models/agent_run.py` (the
  ORM model) — this is the schema layer.
- `diff.py` — `JSONDelta`, `FieldDelta`, `DeltaOp` for change tracking
  (used by the audit system's old/new value capture).
- `audit.py` / `audit_log.py` — `AuditAction`, `AuditEntityType` enums and
  the `AuditLogEntry` response schema.
- `auth.py` — `Permission` enum (role-based access control) and related
  auth schemas. Search here first when adding a new permission-gated
  endpoint.
- `common.py`, `errors.py`, `user.py` — shared primitives, error response
  shapes, user schemas.

Everything is re-exported from `core/schemas/__init__.py` — prefer
`from core.schemas import Restaurant, Dish, ...` over deep imports.

## core/config/

- `settings.py` — `Settings` (pydantic-settings, env-driven), shared by
  every Python service (api, worker) so they agree on connection
  strings/secrets without duplicated parsing. Access via
  `get_settings()` (lru-cached).
- `logging.py` — `configure_logging(settings)`.
- `exceptions.py` — shared exception types.

## core/validation/

Shared validation logic that doesn't belong to one specific schema.
Currently minimal — check here before adding cross-cutting validation
rules rather than duplicating them per-schema.
