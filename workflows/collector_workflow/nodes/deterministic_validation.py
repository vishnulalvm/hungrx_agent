"""Deterministic Validation node: runs non-AI, rule-based checks against
`structured_json` (schema conformance via the strict core.schemas models,
required-field checks, plausibility bounds on nutrition values, etc.) and
records a ValidationResult. "Deterministic" is the operative word — no
model call happens here, only code the team can reason about and unit
test directly. Placeholder for now.
"""

from typing import Any

from workflows.collector_workflow.state import CollectorState


async def deterministic_validation_node(state: CollectorState) -> dict[str, Any]:
    return {
        "errors": [
            {
                "node": "deterministic_validation",
                "message": "deterministic_validation_node is a placeholder — not yet implemented",
            }
        ]
    }
