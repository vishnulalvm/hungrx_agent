"""Human Review node: surfaces the validated ProposedChange(s) to a
reviewer (REVIEWER/DATA_MANAGER/SUPER_ADMIN role, per the RBAC system) and
waits for a decision. This is the graph's human-in-the-loop interrupt
point — the real implementation will use LangGraph's interrupt mechanism
so the run pauses here until the admin dashboard's review-queue UI posts
a decision back, rather than looping/polling inside the node itself.
Placeholder for now; `human_approval_status` stays unset until that lands.
"""

from typing import Any

from workflows.collector_workflow.state import CollectorState


async def human_review_node(state: CollectorState) -> dict[str, Any]:
    return {
        "errors": [
            {"node": "human_review", "message": "human_review_node is a placeholder — not yet implemented"}
        ]
    }
