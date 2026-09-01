"""PATCH-style delta application: given an approved core.schemas.diff.
JSONDelta and the fully validated target Restaurant it was computed
against, mutates ONLY the production rows the delta actually touched —
restaurant-level scalar columns, and specific dish rows (insert/update/
delete) — rather than deleting and re-inserting the whole tree.

This is what "apply approved PATCH-style updates transactionally" means
literally: an untouched dish's row is never even flushed, let alone
deleted and recreated with a new physical row identity. Everything below
still runs inside the caller's existing SQLAlchemy session/transaction —
nothing here calls commit() — so a failure partway through still leaves
nothing partially applied once the caller rolls back, same transactional
guarantee workflows/collector_workflow/nodes/publish.py documents.

Only DeltaOp.ADDED/CHANGED entries whose path resolves to a real dish
(or a restaurant-level scalar field) are actionable; a REMOVED dish path
resolves to a delete of that dish's row. Anything this module can't
confidently resolve to a specific row (a change nested inside something
this patcher doesn't understand) is intentionally left alone — a patch
applier that silently no-ops on the unresolvable is much safer than one
that guesses at what to touch, since the whole point of PATCH-style
application is precision.
"""

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.diff import DeltaOp, JSONDelta
from core.schemas.menu import Dish as DishSchema
from core.schemas.menu import Ingredient
from core.schemas.nutrition import Nutrition
from core.schemas.restaurant import Restaurant as RestaurantSchema
from database.models.restaurant import Dish as DishRow
from database.models.restaurant import Restaurant as RestaurantRow

_DISH_PATH_RE = re.compile(r"^menus\[(\d+)\]\.categories\[(\d+)\](?:\.children\[\d+\])*\.dishes\[(\d+)\]")

_RESTAURANT_SCALAR_FIELDS = {
    "description",
    "logo_url",
    "cover_image_url",
    "cuisine_types",
    "website_url",
    "gallery_image_urls",
}


def _dish_id_from_value(value: Any) -> uuid.UUID | None:
    """A dish-shaped delta value carries the full dish dict (ADDED, or a
    CHANGED entry DeepDiff resolved as a whole-item replacement) — pull
    its id out. A leaf-field CHANGED entry (e.g. only `.price` differs)
    has a scalar old_value/new_value, not a dish dict, so this returns
    None and the caller falls back to resolving the dish id from the
    target tree by path instead."""
    if isinstance(value, dict) and "id" in value and "category_id" in value:
        try:
            return uuid.UUID(str(value["id"]))
        except ValueError:
            return None
    return None


def _find_dish_in_target(target: RestaurantSchema, *, menu_index: int, category_index: int) -> list[DishSchema]:
    """Returns the dish list at the given menu/top-level-category index
    in the fully validated target tree, or [] if the index is out of
    range (shouldn't happen for a delta computed against this same
    target, but never raises on a mismatch — falls back to a no-op for
    that entry instead)."""
    if menu_index >= len(target.menus):
        return []
    categories = target.menus[menu_index].categories
    if category_index >= len(categories):
        return []
    return categories[category_index].dishes


def _dish_row_from_schema(dish: DishSchema, *, category_id: uuid.UUID) -> DishRow:
    return DishRow(
        id=dish.id,
        category_id=category_id,
        name=dish.name,
        description=dish.description,
        image_url=dish.image_url,
        nutrition=dish.nutrition.model_dump(mode="json"),
        allergens=[allergen.value for allergen in dish.allergens],
        ingredients=[ingredient.model_dump(mode="json") for ingredient in dish.ingredients],
        quantity=dish.quantity,
        price=dish.price,
        currency=dish.currency,
        review_state=dish.review_state,
    )


def _apply_dish_row_fields(row: DishRow, dish: DishSchema) -> None:
    row.name = dish.name
    row.description = dish.description
    row.image_url = dish.image_url
    row.nutrition = dish.nutrition.model_dump(mode="json")
    row.allergens = [allergen.value for allergen in dish.allergens]
    row.ingredients = [ingredient.model_dump(mode="json") for ingredient in dish.ingredients]
    row.quantity = dish.quantity
    row.price = dish.price
    row.currency = dish.currency
    row.review_state = dish.review_state


async def apply_patch(
    session: AsyncSession, *, restaurant_row: RestaurantRow, target: RestaurantSchema, delta: JSONDelta
) -> None:
    """Applies `delta`'s ADDED/CHANGED/REMOVED entries to `restaurant_row`
    (an already-loaded, already-attached-to-`session` ORM row) — patching
    restaurant-level scalar columns and touching only the specific dish
    rows the delta names, in place. `target` is the fully validated
    restaurant the delta was computed against (state["validated_
    structured_json"]) — the source of truth for what a touched dish's
    new field values actually are; `delta` only tells this function
    WHICH dishes/fields to touch, not always what to set them to (a
    leaf-field CHANGED entry has the new value inline, but resolving a
    whole dish's row still reads every field from `target`, so a dish
    row never ends up a mix of old-and-new field values)."""
    touched_dish_paths: dict[tuple[int, int, int], DeltaOp] = {}
    restaurant_scalar_touched: set[str] = set()

    for field in delta.fields:
        top_level = field.path.split(".")[0].split("[")[0]
        if top_level in _RESTAURANT_SCALAR_FIELDS:
            restaurant_scalar_touched.add(top_level)
            continue

        match = _DISH_PATH_RE.match(field.path)
        if match is None:
            continue
        menu_index, category_index, dish_index = (int(group) for group in match.groups())
        key = (menu_index, category_index, dish_index)
        # A dish can appear in more than one FieldDelta (e.g. two leaf
        # fields both changed) — REMOVED always wins if seen at all;
        # otherwise ADDED/CHANGED are equivalent for row-resolution
        # purposes (both mean "read this dish's full row from target").
        if key not in touched_dish_paths or field.op == DeltaOp.REMOVED:
            touched_dish_paths[key] = field.op

    for scalar_field in restaurant_scalar_touched:
        setattr(restaurant_row, scalar_field, getattr(target, scalar_field))

    for (menu_index, category_index, dish_index), op in touched_dish_paths.items():
        if op == DeltaOp.REMOVED:
            # The dish no longer exists in `target` by definition (it was
            # removed) — its id has to come from the delta's own
            # old_value, not from indexing into target.
            removed_field = next(
                (
                    f
                    for f in delta.fields
                    if f.op == DeltaOp.REMOVED
                    and _DISH_PATH_RE.match(f.path)
                    and tuple(int(g) for g in _DISH_PATH_RE.match(f.path).groups())
                    == (menu_index, category_index, dish_index)
                ),
                None,
            )
            dish_id = _dish_id_from_value(removed_field.old_value) if removed_field else None
            if dish_id is None:
                continue
            row = await session.get(DishRow, dish_id)
            if row is not None:
                await session.delete(row)
            continue

        dishes = _find_dish_in_target(target, menu_index=menu_index, category_index=category_index)
        if dish_index >= len(dishes):
            continue
        dish = dishes[dish_index]

        existing_row = await session.get(DishRow, dish.id)
        if existing_row is None:
            session.add(_dish_row_from_schema(dish, category_id=dish.category_id))
        else:
            _apply_dish_row_fields(existing_row, dish)

    await session.flush()
