# core/validation/

Deterministic, rule-based validation engine for extracted restaurant
data — no LLM call, no randomness, no I/O. Same input always produces
the same `ValidationOutcome`. This is what the collector workflow's
Deterministic Validation node
(`workflows/collector_workflow/nodes/deterministic_validation.py`) wraps;
this module itself has no dependency on LangGraph, the database, or
`infrastructure/ai/` — it can be called and unit-tested completely on
its own.

## Entry point

`engine.py` — `validate(payload: dict | Restaurant) -> ValidationOutcome`.
Accepts either a raw dict (e.g. `CollectorState["structured_json"]`) or
an already-constructed `Restaurant`. Runs, in order:

1. Schema (re-)validation against `core.schemas.restaurant.Restaurant`
   (`schema_validation.py`) — short-circuits immediately if the payload
   doesn't even parse, since nothing else is safe to check on an
   unparseable shape.
2. Safe corrections (`safe_corrections.py`) — applied **before** the rule
   checks below run, so a rule check never reports a stale issue that a
   correction already fixed (e.g. a lowercase currency code is corrected
   first, so `check_price` never sees it as a problem).
3. Every rule module, walking the full menu → category (recursive) →
   dish tree:
   - `required_fields.py` — missing locations/menus/price/nutrition.
   - `nutrition_rules.py` — serving-size presence, the Atwater cross-check
     (`calories ≈ 4·protein_g + 4·carbohydrates_g + 9·fat_g`, generous
     tolerance for real-world label imprecision), implausible calorie/
     sodium bounds.
   - `allergen_rules.py` — cross-references ingredient names against a
     fixed keyword→`Allergen` map to flag likely-undeclared allergens
     (warning only, never auto-added).
   - `price_rules.py` — implausible price bounds, zero-price flag,
     malformed/lowercase currency codes.
   - `duplicate_detection.py` — exact (case/whitespace-normalized) dish
     name collisions anywhere in the restaurant, not fuzzy matching.
   - `impossible_value_detection.py` — cross-field impossibilities a
     single-field bound can't express (saturated/trans fat exceeding
     total fat, sugar/fiber exceeding total carbohydrates).

## Result shape (`result.py`)

- `ValidationIssue` — `field_path`, `code` (stable machine-readable id),
  `message`, `severity` (`error`/`warning`).
- `CorrectedField` — `field_path`, `old_value`, `new_value`, `reason` —
  one record per correction actually applied.
- `ValidationOutcome` — `errors`, `warnings`, `corrected_fields`,
  `corrected_restaurant` (the `Restaurant` with corrections applied;
  `None` only when schema validation itself failed). `is_valid` is a
  derived property: `True` iff `errors` is empty — **warnings never
  affect validity**, since a warning is by definition something for a
  human to look at, not something that should block the pipeline.

## Never silently changes AI-generated data

This is the load-bearing rule for the whole module. `safe_corrections.py`
is the **only** place any value is rewritten, and it is deliberately
narrow: pure formatting/normalization only —

- whitespace collapse in free text (`description`)
- casing normalization of a controlled-vocabulary code (`currency`)
- exact-duplicate removal from a list (`ingredients`)

It never touches a numeric, monetary, or semantically-inferred value —
calories, macros, price, allergens, dish/category names are never
rewritten by this module, no matter how implausible or Atwater-
inconsistent they look. Those cases only ever produce a `ValidationIssue`
(see `tests/unit/test_validation_engine.py::TestSafeCorrections` for
explicit tests asserting calories/price/allergens survive validation
byte-for-byte even when flagged). Every correction that *is* applied is
recorded with its old/new value on `corrected_fields` — never silent,
even though it doesn't require human approval to apply.

## Testing

`tests/unit/test_validation_engine.py` — extensive, pure unit tests (no
DB/network) covering every rule category plus determinism (same input →
identical output across repeated calls) and input-immutability (`validate`
never mutates the `Restaurant` it was given).
`tests/unit/test_deterministic_validation_node.py` — the LangGraph node
wrapper: `validation_result` shape, `structured_json` only replaced when
a correction was actually applied, fail-closed on missing input, and
AgentRun/AuditLog bookkeeping on invalid results.
