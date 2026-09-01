"""Deterministic price validation. `Dish.price` is already constrained to
`>= 0` by Pydantic (a negative price can't construct a Dish), so this
module covers what schema-level `ge=0` can't: plausibility bounds and
currency-code well-formedness.
"""

from decimal import Decimal

from core.schemas.menu import Dish
from core.validation.result import ValidationIssue, ValidationSeverity

# A price above this for a single menu item is treated as implausible
# rather than merely expensive — catches obvious unit errors (e.g. cents
# entered as dollars, a stray extra digit) without rejecting genuinely
# premium items (tasting menus, whole seafood platters, etc.).
_MAX_PLAUSIBLE_PRICE = Decimal(500)

# A recognizable ISO 4217 code is exactly 3 uppercase letters; Dish
# already defaults/validates length via the schema, this only checks the
# letters-only shape (e.g. rejects "US$" or "12$" slipping through).
_VALID_CURRENCY_LENGTH = 3


def check_price(dish: Dish, *, field_prefix: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if dish.price is not None and dish.price > _MAX_PLAUSIBLE_PRICE:
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.price",
                code="implausible_price",
                message=f"Price {dish.price} {dish.currency} exceeds the plausible bound "
                f"for a single menu item ({_MAX_PLAUSIBLE_PRICE}).",
                severity=ValidationSeverity.ERROR,
            )
        )

    if dish.price is not None and dish.price == 0:
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.price",
                code="zero_price",
                message="Price is exactly 0 — confirm this item is actually free/complimentary "
                "rather than a missing price.",
                severity=ValidationSeverity.WARNING,
            )
        )

    if not dish.currency.isalpha() or len(dish.currency) != _VALID_CURRENCY_LENGTH:
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.currency",
                code="malformed_currency_code",
                message=f"'{dish.currency}' is not a well-formed 3-letter currency code.",
                severity=ValidationSeverity.ERROR,
            )
        )
    elif dish.currency != dish.currency.upper():
        issues.append(
            ValidationIssue(
                field_path=f"{field_prefix}.currency",
                code="lowercase_currency_code",
                message=f"Currency code '{dish.currency}' should be uppercase ISO 4217 "
                f"('{dish.currency.upper()}').",
                severity=ValidationSeverity.WARNING,
            )
        )

    return issues
