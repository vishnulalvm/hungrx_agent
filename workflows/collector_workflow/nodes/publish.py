"""Publish node: once a ProposedChange is approved, applies it to the
live restaurant/menu data and marks it ProposedChangeStatus.PUBLISHED —
the terminal node of a successful collector run. Placeholder for now;
depends on Human Review actually producing an APPROVED decision first.
"""

from typing import Any

from workflows.collector_workflow.state import CollectorState


async def publish_node(state: CollectorState) -> dict[str, Any]:
    return {
        "errors": [{"node": "publish", "message": "publish_node is a placeholder — not yet implemented"}]
    }
