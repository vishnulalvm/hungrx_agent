"""JSON Delta Generation node (Reviewer Workflow, stage 3): computes a
field-level diff (core.schemas.diff.JSONDelta) between the freshly
re-extracted restaurant (state["reextracted_structured_json"]) and the
currently published one (state["restaurant"]) — this is the first place
in the codebase JSONDelta/FieldDelta actually get produced; every other
schema referencing them (core.schemas.proposed_change.ProposedChange)
was defined ahead of the workflow that fills them in.

Uses DeepDiff (already a project dependency, see pyproject.toml) over
the two restaurants' `model_dump(mode="json")` dicts rather than a
hand-rolled tree-walk — DeepDiff already handles list reordering and
added/removed keys correctly, and its per-change paths map directly onto
FieldDelta.path. `iterable_compare_func` (`_compare_by_id`) matches list
items (menus/categories/dishes, all of which carry a stable `id`) by
identity rather than position, so a dish that moved position — or a
sibling being added/removed — doesn't make every other item in the same
list look "changed." Granularity for a *scalar* field changing on an
otherwise-matched item (e.g. only `.price` differs on the same dish) is
one FieldDelta per changed leaf when DeepDiff's report considers the
item a genuine values_changed pair; a matched item whose diff DeepDiff
itself resolves as a full replacement is reported as one CHANGED
FieldDelta carrying the whole before/after item rather than a per-field
breakdown — still fully accurate (nothing is lost, a reviewer sees the
complete old/new item), just coarser than a leaf-level diff in that
specific case.

A delta with zero fields (the re-extraction agreed with what's already
published, even though the raw page bytes hashed differently — e.g. a
timestamp in a footer, whitespace, an ad slot) is still a valid, useful
outcome: it means nothing about the restaurant's actual data changed, so
Delta Validation and Human Final Sync still run against an empty delta
rather than the graph short-circuiting a second time here. Only the hash
check in Temporal Hash Polling is allowed to stop the run early — this
node's job is only to compute the delta, not to decide whether it's
worth reviewing.
"""

import logging
from typing import Any, Awaitable, Callable

from deepdiff import DeepDiff

from core.schemas.diff import DeltaOp, FieldDelta, JSONDelta
from core.schemas.restaurant import Restaurant
from workflows.reviewer_workflow.state import ReviewerState

logger = logging.getLogger("hungrx.workflows.reviewer.json_delta_generation")

NODE_NAME = "json_delta_generation"

JSONDeltaGenerationNode = Callable[[ReviewerState], Awaitable[dict[str, Any]]]

# Identity/bookkeeping fields that differ by construction (new ids get
# assigned per re-extraction pass, timestamps always move) and would
# otherwise show up as noise on every single run regardless of whether
# anything a reviewer cares about actually changed.
_IGNORED_TOP_LEVEL_FIELDS = ("id", "created_at", "updated_at")


def _op_for(diff_kind: str) -> DeltaOp | None:
    if diff_kind in ("dictionary_item_added", "iterable_item_added"):
        return DeltaOp.ADDED
    if diff_kind in ("dictionary_item_removed", "iterable_item_removed"):
        return DeltaOp.REMOVED
    if diff_kind in ("values_changed", "type_changes"):
        return DeltaOp.CHANGED
    return None


def _dotted_path(deepdiff_path: str) -> str:
    """Converts DeepDiff's `root['menus'][0]['name']`-style path into the
    dotted/bracketed shape core.schemas.diff.FieldDelta.path documents
    (e.g. "menus[0].name") — same convention core/validation/engine.py
    already uses for field_path, so a reviewer UI doesn't have to learn
    two different path grammars."""
    without_root = deepdiff_path.removeprefix("root")
    parts: list[str] = []
    token = ""
    i = 0
    while i < len(without_root):
        char = without_root[i]
        if char == "[":
            end = without_root.index("]", i)
            key = without_root[i + 1 : end]
            if key.startswith("'") and key.endswith("'"):
                if token:
                    parts.append(token)
                    token = ""
                parts.append(key[1:-1])
            else:
                parts.append(f"[{key}]")
            i = end + 1
        else:
            token += char
            i += 1
    if token:
        parts.append(token)

    result = ""
    for part in parts:
        if part.startswith("["):
            result += part
        elif result:
            result += f".{part}"
        else:
            result = part
    return result


def _compare_by_id(item1: Any, item2: Any, level=None) -> bool:
    """DeepDiff's iterable_compare_func: matches list items (menus,
    categories, dishes — everything in this tree carries a stable `id`)
    by identity rather than by position, so a dish that moved position
    in the list (or a sibling that was added/removed) doesn't make
    DeepDiff report every other item in the list as "changed" too. Falls
    back to direct equality for items with no `id` key."""
    if isinstance(item1, dict) and isinstance(item2, dict) and "id" in item1 and "id" in item2:
        return item1["id"] == item2["id"]
    return item1 == item2


def compute_delta(current: Restaurant, reextracted: Restaurant) -> JSONDelta:
    """Pure diff computation — no I/O, no state — kept separate from the
    node wrapper so it's directly unit-testable against plain Restaurant
    instances."""
    current_dict = current.model_dump(mode="json")
    reextracted_dict = reextracted.model_dump(mode="json")

    diff = DeepDiff(
        current_dict,
        reextracted_dict,
        ignore_order=True,
        verbose_level=2,
        iterable_compare_func=_compare_by_id,
    )

    fields: list[FieldDelta] = []

    for kind, changes in diff.to_dict().items():
        op = _op_for(kind)
        if op is None:
            continue

        if isinstance(changes, dict):
            items = changes.items()
        else:  # PrettyOrderedSet of paths for added/removed
            items = ((path, None) for path in changes)

        for path, change in items:
            dotted = _dotted_path(path)
            if dotted.split(".")[0].split("[")[0] in _IGNORED_TOP_LEVEL_FIELDS:
                continue

            if op == DeltaOp.CHANGED and isinstance(change, dict):
                old_value = change.get("old_value")
                new_value = change.get("new_value")
            elif op == DeltaOp.ADDED:
                old_value = None
                new_value = _lookup(reextracted_dict, dotted)
            elif op == DeltaOp.REMOVED:
                old_value = _lookup(current_dict, dotted)
                new_value = None
            else:
                old_value, new_value = None, None

            fields.append(
                FieldDelta(
                    path=dotted,
                    op=op,
                    old_value=old_value,
                    new_value=new_value,
                )
            )

    return JSONDelta(fields=fields)


def _lookup(data: Any, dotted_path: str) -> Any:
    """Best-effort read of `dotted_path` (e.g. "menus[0].name") back out
    of the dumped dict, for added/removed entries where DeepDiff itself
    doesn't hand back a value directly."""
    current: Any = data
    token = ""
    i = 0
    path = dotted_path
    tokens: list[str] = []
    while i < len(path):
        char = path[i]
        if char == ".":
            if token:
                tokens.append(token)
                token = ""
            i += 1
        elif char == "[":
            if token:
                tokens.append(token)
                token = ""
            end = path.index("]", i)
            tokens.append(path[i + 1 : end])
            i = end + 1
        else:
            token += char
            i += 1
    if token:
        tokens.append(token)

    try:
        for tok in tokens:
            if isinstance(current, list):
                current = current[int(tok)]
            else:
                current = current[tok]
        return current
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def build_json_delta_generation_node() -> JSONDeltaGenerationNode:
    async def json_delta_generation_node(state: ReviewerState) -> dict[str, Any]:
        restaurant = state.get("restaurant")
        reextracted_json = state.get("reextracted_structured_json")

        if restaurant is None or reextracted_json is None:
            message = (
                "ReviewerState.restaurant/reextracted_structured_json are required before "
                "json_delta_generation runs (targeted_reextraction must succeed first)"
            )
            logger.error("json_delta_generation node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        reextracted = Restaurant.model_validate(reextracted_json)
        delta = compute_delta(restaurant, reextracted)

        return {"delta": delta}

    return json_delta_generation_node
