"""Impossible-value detection: catches values that are not merely
"unusual" but physically/logically impossible given other fields on the
same object — the kind of thing schema-level `ge=0` bounds can't express
because they only look at one field at a time.

Nutrition/price-specific plausibility bounds already live in
nutrition_rules.py/price_rules.py (single-field "too large" checks);
this module is specifically for cross-field impossibilities.
"""

from core.schemas.menu import Dish
from core.validation.result import ValidationIssue, ValidationSeverity


def check_impossible_values(dish: Dish, *, field_prefix: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    macros = dish.nutrition.macros

    if (
        macros.saturated_fat_g is not None
        and macros.fat_g is not None
        and macros.saturated_fat_g > macros.fat_g
    ):
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.macros.saturated_fat_g",
                code="saturated_fat_exceeds_total_fat",
                message=f"saturated_fat_g ({macros.saturated_fat_g}) cannot exceed "
                f"fat_g ({macros.fat_g}) — saturated fat is a subset of total fat.",
                severity=ValidationSeverity.ERROR,
            )
        )

    if (
        macros.trans_fat_g is not None
        and macros.fat_g is not None
        and macros.trans_fat_g > macros.fat_g
    ):
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.macros.trans_fat_g",
                code="trans_fat_exceeds_total_fat",
                message=f"trans_fat_g ({macros.trans_fat_g}) cannot exceed "
                f"fat_g ({macros.fat_g}) — trans fat is a subset of total fat.",
                severity=ValidationSeverity.ERROR,
            )
        )

    if (
        macros.sugar_g is not None
        and macros.carbohydrates_g is not None
        and macros.sugar_g > macros.carbohydrates_g
    ):
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.macros.sugar_g",
                code="sugar_exceeds_total_carbohydrates",
                message=f"sugar_g ({macros.sugar_g}) cannot exceed "
                f"carbohydrates_g ({macros.carbohydrates_g}) — sugar is a subset of "
                "total carbohydrates.",
                severity=ValidationSeverity.ERROR,
            )
        )

    if (
        macros.fiber_g is not None
        and macros.carbohydrates_g is not None
        and macros.fiber_g > macros.carbohydrates_g
    ):
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.macros.fiber_g",
                code="fiber_exceeds_total_carbohydrates",
                message=f"fiber_g ({macros.fiber_g}) cannot exceed "
                f"carbohydrates_g ({macros.carbohydrates_g}) — fiber is a subset of "
                "total carbohydrates.",
                severity=ValidationSeverity.ERROR,
            )
        )

    return issues
