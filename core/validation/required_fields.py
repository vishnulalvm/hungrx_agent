"""Required-field checks beyond what Pydantic's own field constraints
already enforce. Restaurant/Menu/MenuCategory/Dish `name` fields are
already non-blank per the schema (a blank name can't construct the
object at all) — this module covers fields that are schema-optional but
practically required for the data to be useful downstream (e.g. a dish
with no price at all, a restaurant with no locations)."""

from core.schemas.menu import Dish
from core.schemas.restaurant import Restaurant
from core.validation.result import ValidationIssue, ValidationSeverity


def check_required_fields(restaurant: Restaurant) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not restaurant.locations:
        issues.append(
            ValidationIssue(
                field_path="locations",
                code="missing_locations",
                message="Restaurant has no locations.",
                severity=ValidationSeverity.WARNING,
            )
        )

    if not restaurant.menus:
        issues.append(
            ValidationIssue(
                field_path="menus",
                code="missing_menus",
                message="Restaurant has no menus.",
                severity=ValidationSeverity.WARNING,
            )
        )

    return issues


def check_dish_required_fields(dish: Dish, *, field_prefix: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if dish.price is None:
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.price",
                code="missing_price",
                message=f"Dish '{dish.name}' has no price.",
                severity=ValidationSeverity.WARNING,
            )
        )

    has_any_nutrition = any(
        value is not None
        for value in (
            dish.nutrition.macros.calories,
            dish.nutrition.macros.protein_g,
            dish.nutrition.macros.carbohydrates_g,
            dish.nutrition.macros.fat_g,
        )
    )
    if not has_any_nutrition:
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.nutrition",
                code="missing_nutrition",
                message=f"Dish '{dish.name}' has no nutrition data.",
                severity=ValidationSeverity.WARNING,
            )
        )

    return issues
