"""Duplicate detection: flags dishes that look like the same menu item
listed twice — most often an artifact of the crawler capturing the same
page content twice (e.g. once from the root page's embedded menu, once
from a linked "menu" page — see infrastructure/crawler/page_discovery.py)
or of the AI re-emitting an item across two source snapshots.

Matching is deliberately case-insensitive/whitespace-normalized-exact on
name (not fuzzy) — a fuzzy matcher risks flagging two genuinely different
dishes ("Chicken Sandwich" / "Chicken Sandwich Combo") as duplicates,
which is worse than missing a true duplicate that a human will also
catch during review.
"""

from collections import defaultdict

from core.schemas.menu import Dish
from core.validation.result import ValidationIssue, ValidationSeverity


def _normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def check_duplicate_dishes(dishes: list[tuple[Dish, str]]) -> list[ValidationIssue]:
    """`dishes` is a list of (Dish, field_path) pairs — the caller
    (engine.py) is responsible for walking the category tree and
    supplying each dish's full path, since this module has no notion of
    tree position. Every dish sharing a normalized name with another
    dish anywhere in the same restaurant is flagged; duplicates across
    different categories/menus are still duplicates, since the UI is
    just as likely to show the same item twice regardless of which
    category each copy landed in."""
    by_name: dict[str, list[str]] = defaultdict(list)
    for dish, field_path in dishes:
        by_name[_normalize_name(dish.name)].append(field_path)

    issues: list[ValidationIssue] = []
    for normalized_name, field_paths in by_name.items():
        if len(field_paths) < 2:
            continue
        for field_path in field_paths:
            issues.append(
                ValidationIssue(
                    field_path=f"{field_path}.name",
                    code="duplicate_dish_name",
                    message=f"'{normalized_name}' appears {len(field_paths)} times "
                    f"({', '.join(field_paths)}); these may be duplicates.",
                    severity=ValidationSeverity.WARNING,
                )
            )

    return issues
