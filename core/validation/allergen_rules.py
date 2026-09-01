"""Allergen taxonomy checks. `Dish.allergens` is already constrained to
`core.schemas.menu.Allergen` by Pydantic (an invalid allergen value can't
even construct a Dish), so the taxonomy-membership check itself mostly
documents/re-confirms that guarantee. The check that actually adds value
here is cross-referencing declared ingredients against a fixed
keyword-to-allergen map: if an ingredient obviously implies an allergen
that isn't declared, that's flagged as a WARNING for a human to confirm
— never auto-added, since inferring an allergen from an ingredient name
is exactly the kind of guess this validator must not silently apply to
someone's safety-relevant data.
"""

from core.schemas.menu import Allergen, Dish
from core.validation.result import ValidationIssue, ValidationSeverity

# Deliberately narrow, high-confidence keyword -> allergen mappings only
# (no fuzzy/partial matching, no synonyms likely to false-positive) —
# this is meant to catch obvious omissions, not to be an exhaustive
# ingredient-allergen database.
_INGREDIENT_ALLERGEN_KEYWORDS: dict[str, Allergen] = {
    "milk": Allergen.MILK,
    "cheese": Allergen.MILK,
    "butter": Allergen.MILK,
    "cream": Allergen.MILK,
    "yogurt": Allergen.MILK,
    "egg": Allergen.EGGS,
    "mayonnaise": Allergen.EGGS,
    "shrimp": Allergen.SHELLFISH,
    "crab": Allergen.SHELLFISH,
    "lobster": Allergen.SHELLFISH,
    "peanut": Allergen.PEANUTS,
    "almond": Allergen.TREE_NUTS,
    "walnut": Allergen.TREE_NUTS,
    "cashew": Allergen.TREE_NUTS,
    "pecan": Allergen.TREE_NUTS,
    "wheat": Allergen.WHEAT,
    "flour": Allergen.WHEAT,
    "soy": Allergen.SOY,
    "tofu": Allergen.SOY,
    "sesame": Allergen.SESAME,
    "tahini": Allergen.SESAME,
}


def check_allergens(dish: Dish, *, field_prefix: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    declared = set(dish.allergens)

    implied = _implied_allergens(dish)
    undeclared = sorted((implied - declared), key=lambda allergen: allergen.value)

    for allergen in undeclared:
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.allergens",
                code="possible_undeclared_allergen",
                message=f"Ingredients suggest '{allergen.value}' may be present but it is not "
                "declared in allergens — please confirm.",
                severity=ValidationSeverity.WARNING,
            )
        )

    return issues


def _implied_allergens(dish: Dish) -> set[Allergen]:
    implied: set[Allergen] = set()
    for ingredient in dish.ingredients:
        lowered = ingredient.name.lower()
        for keyword, allergen in _INGREDIENT_ALLERGEN_KEYWORDS.items():
            if keyword in lowered:
                implied.add(allergen)
    return implied
