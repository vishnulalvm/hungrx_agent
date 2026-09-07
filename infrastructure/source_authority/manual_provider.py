"""An EntityResolutionProvider that returns exactly one pre-verified
candidate: the URL an admin already supplied and that
apps/api/app/services/manual_source_verification.py already validated and
persisted for this restaurant. Used to let the collector workflow's own
source_authority_node (workflows/collector_workflow/nodes/source_authority.py,
unmodified) skip search for manually-ingested restaurants: it's still the
same node, still creates its own AgentRun, still calls
SourceAuthorityService.resolve_official_website — just against a provider
that already knows the one right answer instead of one that has to look
for it.
"""

from core.schemas.source_authority import EntityCandidate, EntityResolutionQuery
from infrastructure.source_authority.provider import EntityResolutionProvider

# provider_confidence=1.0 is what pushes SourceAuthorityService's own
# confidence-threshold logic (_confidence_level >= 0.75 -> HIGH ->
# auto-verify) to classify this candidate VERIFIED with zero special
# casing anywhere else in the resolution/persistence pipeline.
_MANUAL_CONFIDENCE = 1.0
_PROVIDER_NAME = "manual"


class ManualUrlEntityResolutionProvider(EntityResolutionProvider):
    def __init__(self, url: str) -> None:
        self._url = url

    async def resolve(self, query: EntityResolutionQuery) -> list[EntityCandidate]:
        return [
            EntityCandidate(
                url=self._url, provider_confidence=_MANUAL_CONFIDENCE, provider_name=_PROVIDER_NAME
            )
        ]
