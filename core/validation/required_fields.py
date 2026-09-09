"""Required-field checks beyond what Pydantic's own field constraints
already enforce. Restaurant/Menu/MenuCategory/Dish `name` fields are
already non-blank per the schema (a blank name can't construct the
object at all) — this module covers fields that are schema-optional but
practically required for the data to be useful downstream (e.g. a dish
with no price at all, a restaurant with no locations).

`missing_menus` is an ERROR, not a WARNING, unlike every other check in
this module: a restaurant with zero menus almost always means
extraction/AI translation silently found nothing usable (e.g. the
crawled page was a soft-404 or bot-challenge page, not real menu
content) rather than a restaurant that genuinely has no menu — see
workflows/collector_workflow/graph.py's module docstring for why an
ERROR here matters structurally, not just cosmetically: it routes the
run straight to END instead of creating a ProposedChange that would
otherwise sit in the review queue looking like a normal, valid "nothing
to see here" case for a human to approve. `missing_locations` stays a
WARNING — a restaurant can legitimately have real menu data collected
before its physical locations are, so an empty locations list alone
isn't the same "extraction produced nothing" signal."""

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
                message="Restaurant has no menus — extraction likely found no usable menu content.",
                severity=ValidationSeverity.ERROR,
            )
        )
    elif not any(_menu_has_any_dish(menu) for menu in restaurant.menus):
        # Menus/categories exist but every one is empty — the same
        # "extraction found nothing" failure as missing_menus, just
        # nested one level deeper (e.g. discovery found category names
        # but every per-category dish call came back empty).
        issues.append(
            ValidationIssue(
                field_path="menus",
                code="empty_menus",
                message="Restaurant has menus but no dishes in any of them — "
                "extraction likely found no usable menu content.",
                severity=ValidationSeverity.ERROR,
            )
        )

    return issues


def _menu_has_any_dish(menu) -> bool:
    return any(_category_has_any_dish(category) for category in menu.categories)


def _category_has_any_dish(category) -> bool:
    if category.dishes:
        return True
    return any(_category_has_any_dish(child) for child in category.children)


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
