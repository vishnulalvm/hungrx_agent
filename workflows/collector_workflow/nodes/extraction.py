"""Extraction node: runs the AI extraction pass over a crawled
SourceSnapshot to produce a raw ExtractionResult. Explicitly out of scope
for this task ("do not add AI extraction yet" / "do not implement all
node logic yet") — placeholder only, so the graph has a real node to wire
the LLM call into later.
"""

from typing import Any

from workflows.collector_workflow.state import CollectorState


async def extraction_node(state: CollectorState) -> dict[str, Any]:
    return {
        "errors": [
            {"node": "extraction", "message": "extraction_node is a placeholder — AI extraction not yet implemented"}
        ]
    }
