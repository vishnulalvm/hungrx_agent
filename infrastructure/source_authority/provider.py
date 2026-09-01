"""Entity resolution provider interface.

The actual lookup — a places/business-listing API, a search engine, a
knowledge-graph provider, whatever is chosen later — stays entirely behind
this interface. SourceAuthorityService depends only on
`EntityResolutionProvider`, never on a concrete provider, so swapping the
backing service (or adding a second one to cross-check against) never
touches the resolution/validation/persistence logic.
"""

from abc import ABC, abstractmethod

from core.schemas.source_authority import EntityCandidate, EntityResolutionQuery


class EntityResolutionProvider(ABC):
    @abstractmethod
    async def resolve(self, query: EntityResolutionQuery) -> list[EntityCandidate]:
        """Returns candidate official-website URLs for the given
        restaurant identity, ranked by the provider's own confidence
        (best first is a convention, not a requirement — the caller
        re-sorts by provider_confidence regardless)."""
