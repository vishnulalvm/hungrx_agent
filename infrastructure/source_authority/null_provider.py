"""A provider that always returns no candidates.

Exists so SourceAuthorityService is fully constructible and testable
without wiring up a real external API/key — the default until a real
provider (Google Places, a search API, etc.) is implemented against the
`EntityResolutionProvider` interface and swapped in. Every query resolves
to ResolutionStatus.NOT_FOUND, never a false positive.
"""

from core.schemas.source_authority import EntityCandidate, EntityResolutionQuery
from infrastructure.source_authority.provider import EntityResolutionProvider


class NullEntityResolutionProvider(EntityResolutionProvider):
    async def resolve(self, query: EntityResolutionQuery) -> list[EntityCandidate]:
        return []
