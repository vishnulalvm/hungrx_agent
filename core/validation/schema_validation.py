"""Pydantic (re-)validation: confirms a Restaurant-shaped payload
actually conforms to core.schemas.restaurant.Restaurant. In the collector
workflow, `structured_json` is already produced by constructing a real
`Restaurant` instance (see multimodal_translation.py), so this mostly
catches corruption introduced between nodes (e.g. a future node that
edits the dict directly) or validates payloads coming from any other
future caller (e.g. a human-authored ProposedChange) that isn't
guaranteed to have gone through Restaurant construction already.

Kept as its own module (rather than inlined in engine.py) so the "is this
even shape-valid" check is clearly separated from the semantic rule
checks (nutrition/allergen/price/etc.) that only make sense to run once
the shape is already known-good.
"""

from pydantic import ValidationError

from core.schemas.restaurant import Restaurant
from core.validation.result import ValidationIssue, ValidationSeverity


def validate_schema(payload: dict) -> tuple[Restaurant | None, list[ValidationIssue]]:
    """Returns (parsed Restaurant, []) on success, or (None, issues) if
    `payload` doesn't conform to the Restaurant schema. Every Pydantic
    validation error becomes one ValidationIssue with a dotted field_path
    built from Pydantic's own error location, so issues stay traceable to
    the exact offending field."""
    try:
        restaurant = Restaurant.model_validate(payload)
    except ValidationError as exc:
        issues = [
            ValidationIssue(
                field_path=".".join(str(part) for part in error["loc"]) or "<root>",
                code="schema_violation",
                message=error["msg"],
                severity=ValidationSeverity.ERROR,
            )
            for error in exc.errors()
        ]
        return None, issues

    return restaurant, []
