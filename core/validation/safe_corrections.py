"""Deterministic, safe corrections — the ONLY place in core.validation
that rewrites a value rather than merely reporting an issue about it.

"Safe" is deliberately narrow: a correction is only applied here when it
is a pure formatting/normalization change that cannot alter meaning —
whitespace collapse, casing normalization of a code (not free text),
exact-duplicate removal from a list. Nothing here ever touches a
numeric, monetary, or AI-inferred value (calories, price, allergens,
description text, etc.) — those are exactly the kind of "AI-generated
data" this validator must never silently change; they only ever produce
ValidationIssues for a human to act on (see the other rule modules).

Every correction actually applied is recorded as a CorrectedField so the
caller (and any reviewer UI) can see exactly what changed and why —
corrections are never silent, even though they don't require human
approval to apply.
"""

from core.schemas.menu import Dish
from core.schemas.restaurant import Restaurant
from core.validation.result import CorrectedField


def apply_safe_corrections(restaurant: Restaurant) -> tuple[Restaurant, list[CorrectedField]]:
    """Returns a (possibly) new Restaurant with safe corrections applied,
    plus the list of corrections made. `restaurant` itself is never
    mutated in place — Pydantic models here are treated as immutable
    inputs, consistent with how the rest of the collector workflow
    passes data between nodes."""
    corrections: list[CorrectedField] = []
    updated_menus = []

    for menu_index, menu in enumerate(restaurant.menus):
        updated_categories, menu_corrections = _correct_categories(
            menu.categories, field_prefix=f"menus[{menu_index}]"
        )
        corrections.extend(menu_corrections)
        updated_menus.append(menu.model_copy(update={"categories": updated_categories}))

    corrected_restaurant = restaurant.model_copy(update={"menus": updated_menus})
    return corrected_restaurant, corrections


def _correct_categories(categories: list, *, field_prefix: str) -> tuple[list, list[CorrectedField]]:
    corrections: list[CorrectedField] = []
    updated = []

    for category_index, category in enumerate(categories):
        category_field_prefix = f"{field_prefix}.categories[{category_index}]"

        updated_dishes = []
        for dish_index, dish in enumerate(category.dishes):
            dish_field_prefix = f"{category_field_prefix}.dishes[{dish_index}]"
            corrected_dish, dish_corrections = _correct_dish(dish, field_prefix=dish_field_prefix)
            corrections.extend(dish_corrections)
            updated_dishes.append(corrected_dish)

        updated_children, child_corrections = _correct_categories(
            category.children, field_prefix=category_field_prefix
        )
        corrections.extend(child_corrections)

        updated.append(category.model_copy(update={"dishes": updated_dishes, "children": updated_children}))

    return updated, corrections


def _correct_dish(dish: Dish, *, field_prefix: str) -> tuple[Dish, list[CorrectedField]]:
    corrections: list[CorrectedField] = []
    updates: dict = {}

    # Uppercase a currency code that is otherwise a well-formed 3-letter
    # code — pure casing normalization of a controlled-vocabulary code,
    # never applied to free text like name/description.
    if dish.currency.isalpha() and len(dish.currency) == 3 and dish.currency != dish.currency.upper():
        updates["currency"] = dish.currency.upper()
        corrections.append(
            CorrectedField(
                field_path=f"{field_prefix}.currency",
                old_value=dish.currency,
                new_value=dish.currency.upper(),
                reason="Normalized currency code casing to uppercase ISO 4217.",
            )
        )

    # Collapse internal whitespace runs in description (formatting only —
    # never changes which words are present, unlike trimming/rewriting
    # content would).
    if dish.description is not None:
        collapsed = " ".join(dish.description.split())
        if collapsed != dish.description:
            updates["description"] = collapsed
            corrections.append(
                CorrectedField(
                    field_path=f"{field_prefix}.description",
                    old_value=dish.description,
                    new_value=collapsed,
                    reason="Collapsed redundant whitespace.",
                )
            )

    # Exact-duplicate ingredient entries (same normalized name) collapsed
    # to one — removing a literal repeat is not a semantic change to the
    # ingredient list's meaning.
    deduplicated_ingredients, removed_any = _deduplicate_ingredients(dish.ingredients)
    if removed_any:
        updates["ingredients"] = deduplicated_ingredients
        corrections.append(
            CorrectedField(
                field_path=f"{field_prefix}.ingredients",
                old_value=str([ingredient.name for ingredient in dish.ingredients]),
                new_value=str([ingredient.name for ingredient in deduplicated_ingredients]),
                reason="Removed exact-duplicate ingredient entries.",
            )
        )

    if not updates:
        return dish, []
    return dish.model_copy(update=updates), corrections


def _deduplicate_ingredients(ingredients: list) -> tuple[list, bool]:
    seen: set[str] = set()
    deduplicated = []
    for ingredient in ingredients:
        key = " ".join(ingredient.name.strip().lower().split())
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(ingredient)
    return deduplicated, len(deduplicated) != len(ingredients)
