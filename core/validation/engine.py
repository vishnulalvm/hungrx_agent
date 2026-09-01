"""Deterministic validation engine: the single entry point that runs
every rule module (schema, nutrition, allergen, price, required-field,
duplicate, impossible-value) plus safe corrections against a Restaurant,
and returns one ValidationOutcome.

"Deterministic" means exactly what it says: no model call, no randomness,
no I/O — the same Restaurant input always produces the same
ValidationOutcome. This is what lets `validate()` be trivially unit
tested and reasoned about, independent of whatever AI provider produced
the data in the first place (see workflows/collector_workflow/nodes/
multimodal_translation.py for where that AI-generated data comes from —
this module never imports anything from infrastructure.ai or calls out
to a model).
"""

from core.schemas.menu import Dish, MenuCategory
from core.schemas.restaurant import Restaurant
from core.validation.allergen_rules import check_allergens
from core.validation.duplicate_detection import check_duplicate_dishes
from core.validation.impossible_value_detection import check_impossible_values
from core.validation.nutrition_rules import check_nutrition
from core.validation.price_rules import check_price
from core.validation.required_fields import check_dish_required_fields, check_required_fields
from core.validation.result import ValidationOutcome, ValidationSeverity
from core.validation.safe_corrections import apply_safe_corrections
from core.validation.schema_validation import validate_schema


def _iter_dishes(categories: list[MenuCategory], *, field_prefix: str) -> list[tuple[Dish, str]]:
    """Walks the (arbitrarily deep) category tree and returns every dish
    paired with its full field_path — the tree-walking logic lives here,
    once, rather than being duplicated inside every rule module that
    needs to reach dishes."""
    dishes: list[tuple[Dish, str]] = []
    for category_index, category in enumerate(categories):
        category_field_prefix = f"{field_prefix}.categories[{category_index}]"
        for dish_index, dish in enumerate(category.dishes):
            dishes.append((dish, f"{category_field_prefix}.dishes[{dish_index}]"))
        dishes.extend(_iter_dishes(category.children, field_prefix=category_field_prefix))
    return dishes


def validate(payload: dict | Restaurant) -> ValidationOutcome:
    """Runs the full deterministic validation pipeline.

    `payload` may be a raw dict (e.g. CollectorState["structured_json"])
    or an already-constructed Restaurant. A dict that fails schema
    validation short-circuits immediately — every subsequent rule module
    assumes a schema-valid Restaurant, so there's nothing safe to check
    further once the shape itself is wrong.
    """
    if isinstance(payload, Restaurant):
        restaurant, schema_issues = payload, []
    else:
        restaurant, schema_issues = validate_schema(payload)

    if restaurant is None:
        errors = [issue for issue in schema_issues if issue.severity == ValidationSeverity.ERROR]
        warnings = [issue for issue in schema_issues if issue.severity == ValidationSeverity.WARNING]
        return ValidationOutcome(errors=errors, warnings=warnings, corrected_fields=[])

    corrected_restaurant, corrected_fields = apply_safe_corrections(restaurant)

    all_issues = list(schema_issues)
    all_issues.extend(check_required_fields(corrected_restaurant))

    for menu_index, menu in enumerate(corrected_restaurant.menus):
        menu_field_prefix = f"menus[{menu_index}]"
        dishes = _iter_dishes(menu.categories, field_prefix=menu_field_prefix)

        all_issues.extend(check_duplicate_dishes(dishes))

        for dish, field_path in dishes:
            all_issues.extend(check_dish_required_fields(dish, field_prefix=field_path))
            all_issues.extend(check_nutrition(dish, field_prefix=f"{field_path}.nutrition"))
            all_issues.extend(check_allergens(dish, field_prefix=field_path))
            all_issues.extend(check_price(dish, field_prefix=field_path))
            all_issues.extend(check_impossible_values(dish, field_prefix=field_path))

    errors = [issue for issue in all_issues if issue.severity == ValidationSeverity.ERROR]
    warnings = [issue for issue in all_issues if issue.severity == ValidationSeverity.WARNING]

    return ValidationOutcome(
        errors=errors,
        warnings=warnings,
        corrected_fields=corrected_fields,
        corrected_restaurant=corrected_restaurant,
    )
