"""Deterministic nutrition constraint checks, including the Atwater
general factor cross-check (calories ≈ 4·protein_g + 4·carbohydrates_g +
9·fat_g). Every check here is arithmetic/comparison against fixed
constants — no model call, no heuristic guessing, same input always
produces the same output.

Deliberately conservative about corrections: a calorie/macro mismatch is
reported as a WARNING (a human should look at it — the discrepancy could
mean the source material rounded values, omitted alcohol calories,
listed net vs. total carbs, etc.) and NEVER auto-corrected. Silently
rewriting a calorie or macro figure the AI extracted is exactly the kind
of "silently change AI-generated data" this validator must not do —
Atwater math is a plausibility check, not a source of truth precise
enough to overwrite what was actually printed on a menu/nutrition label.
"""

from decimal import Decimal

from core.schemas.menu import Dish
from core.validation.result import ValidationIssue, ValidationSeverity

# Atwater general factors (kcal per gram).
_KCAL_PER_G_PROTEIN = Decimal(4)
_KCAL_PER_G_CARB = Decimal(4)
_KCAL_PER_G_FAT = Decimal(9)

# Menu/nutrition-label rounding, serving-size estimation, and
# unlisted-but-caloric ingredients (e.g. alcohol, sugar alcohols) mean
# real-world values routinely miss "textbook" Atwater math by a wide
# margin — this tolerance is intentionally loose so the check only flags
# genuinely implausible combinations, not ordinary label imprecision.
_ATWATER_RELATIVE_TOLERANCE = Decimal("0.35")
_ATWATER_MIN_ABSOLUTE_TOLERANCE_KCAL = Decimal(50)

# Values above these are treated as physically implausible for a single
# menu item/serving rather than merely "high" — see
# impossible_value_detection.py for the general framing; these two are
# kept here since they're nutrition-specific bounds.
_MAX_PLAUSIBLE_CALORIES = Decimal(5000)
_MAX_PLAUSIBLE_SODIUM_MG = Decimal(10000)


def _atwater_expected_calories(*, protein_g: Decimal, carbohydrates_g: Decimal, fat_g: Decimal) -> Decimal:
    return protein_g * _KCAL_PER_G_PROTEIN + carbohydrates_g * _KCAL_PER_G_CARB + fat_g * _KCAL_PER_G_FAT


def check_nutrition(dish: Dish, *, field_prefix: str) -> list[ValidationIssue]:
    """Runs every nutrition-related check for one dish. `field_prefix`
    lets the caller (engine.py) build a full path like
    "menus[0].categories[0].dishes[2].nutrition" without this module
    needing to know its position in the tree."""
    issues: list[ValidationIssue] = []
    macros = dish.nutrition.macros

    issues.extend(_check_serving_size_present(dish, field_prefix=field_prefix))
    issues.extend(_check_atwater_consistency(macros, field_prefix=field_prefix))
    issues.extend(_check_plausible_bounds(dish, field_prefix=field_prefix))

    return issues


def _check_serving_size_present(dish: Dish, *, field_prefix: str) -> list[ValidationIssue]:
    macros = dish.nutrition.macros
    has_any_value = any(
        value is not None
        for value in (
            macros.calories,
            macros.protein_g,
            macros.carbohydrates_g,
            macros.fat_g,
        )
    )
    if has_any_value and not dish.nutrition.serving_size:
        return [
            ValidationIssue(
                field_path=f"{field_prefix}.serving_size",
                code="missing_serving_size",
                message="Nutrition values are present but no serving_size is set; "
                "these numbers are meaningless without a serving context.",
                severity=ValidationSeverity.WARNING,
            )
        ]
    return []


def _check_atwater_consistency(macros, *, field_prefix: str) -> list[ValidationIssue]:
    if macros.calories is None:
        return []
    if macros.protein_g is None or macros.carbohydrates_g is None or macros.fat_g is None:
        # Can't cross-check without all three macros present — not an
        # error, just nothing this rule can evaluate.
        return []

    expected = _atwater_expected_calories(
        protein_g=macros.protein_g, carbohydrates_g=macros.carbohydrates_g, fat_g=macros.fat_g
    )
    tolerance = max(_ATWATER_MIN_ABSOLUTE_TOLERANCE_KCAL, expected * _ATWATER_RELATIVE_TOLERANCE)
    difference = abs(macros.calories - expected)

    if difference > tolerance:
        return [
            ValidationIssue(
                field_path=f"{field_prefix}.macros.calories",
                code="atwater_mismatch",
                message=(
                    f"Stated calories ({macros.calories}) diverge from the Atwater estimate "
                    f"from macros ({expected}) by more than the allowed tolerance "
                    f"({tolerance:.0f} kcal). This may indicate a transcription error, "
                    "but the source value is kept as-is for human review."
                ),
                severity=ValidationSeverity.WARNING,
            )
        ]
    return []


def _check_plausible_bounds(dish: Dish, *, field_prefix: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    macros = dish.nutrition.macros

    if macros.calories is not None and macros.calories > _MAX_PLAUSIBLE_CALORIES:
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.macros.calories",
                code="implausible_calories",
                message=f"{macros.calories} kcal exceeds the plausible bound for a single "
                f"menu item ({_MAX_PLAUSIBLE_CALORIES} kcal).",
                severity=ValidationSeverity.ERROR,
            )
        )

    if macros.sodium_mg is not None and macros.sodium_mg > _MAX_PLAUSIBLE_SODIUM_MG:
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.macros.sodium_mg",
                code="implausible_sodium",
                message=f"{macros.sodium_mg} mg sodium exceeds the plausible bound for a "
                f"single menu item ({_MAX_PLAUSIBLE_SODIUM_MG} mg).",
                severity=ValidationSeverity.ERROR,
            )
        )

    return issues
