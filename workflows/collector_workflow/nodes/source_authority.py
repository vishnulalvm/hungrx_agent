"""Source Authority node: resolves the restaurant's verified official
website via SourceAuthorityService (apps/api/app/services/source_authority_service.py)
and records the result on state.

Node functions in this graph deliberately take/return plain dicts (partial
CollectorState updates), not the service objects themselves — callers of
`compile()` wire in whatever session/provider a given run needs via
closures (see graph.py's `build_graph`), keeping the node functions here
free of any FastAPI/DB plumbing.
"""

from typing import Any

from workflows.collector_workflow.state import CollectorState


async def source_authority_node(state: CollectorState) -> dict[str, Any]:
    """Placeholder: real wiring (constructing SourceAuthorityService with a
    live DB session + provider, calling resolve_official_website, and
    mapping SourceAuthorityResult onto `source_url`/`source`) lands with
    the rest of the collector workflow's runtime wiring. For now this node
    exists so the graph has a real first step and compiles end-to-end.
    """
    return {
        "errors": [
            {
                "node": "source_authority",
                "message": "source_authority_node is a placeholder — not yet wired to SourceAuthorityService",
            }
        ]
    }
