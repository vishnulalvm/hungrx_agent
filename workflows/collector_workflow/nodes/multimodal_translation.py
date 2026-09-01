"""Multimodal Translation node: normalizes the raw ExtractionResult (which
may combine text, HTML structure, and image/PDF-derived content) into
`structured_json` — the strictly typed shape (core.schemas.restaurant /
menu / nutrition) the rest of the pipeline validates and diffs against.
Placeholder — depends on the Extraction node landing first.
"""

from typing import Any

from workflows.collector_workflow.state import CollectorState


async def multimodal_translation_node(state: CollectorState) -> dict[str, Any]:
    return {
        "errors": [
            {
                "node": "multimodal_translation",
                "message": "multimodal_translation_node is a placeholder — not yet implemented",
            }
        ]
    }
