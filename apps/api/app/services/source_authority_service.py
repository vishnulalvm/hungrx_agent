"""Source-authority resolution: given a restaurant's identity, produce a
verified official website URL.

Pipeline: query the (interface-hidden) EntityResolutionProvider -> for
each candidate, normalize the URL and reject known aggregators/malformed
domains -> pick the best surviving candidate -> score confidence from the
provider's own score plus a validation boost -> persist a Source record
when a URL clears the auto-trust bar -> always return a typed
SourceAuthorityResult, even on failure, so the caller has structured
"why" rather than a bare None.

Deliberately stops at producing/persisting the Source record — crawling
it, extracting menu data, etc. belong to the collector workflow, a
separate future task.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.source import SourceType
from core.schemas.source_authority import (
    ConfidenceLevel,
    EntityCandidate,
    EntityResolutionQuery,
    ResolutionStatus,
    SourceAuthorityResult,
)
from database.repositories.source_repository import SourceRepository
from infrastructure.source_authority.domain_validator import DomainRejectedError, validate_official_domain
from infrastructure.source_authority.provider import EntityResolutionProvider

# A validated candidate auto-trusts (persists as verified) only above this
# provider-confidence bar. Below it, the caller gets NEEDS_REVIEW with the
# best candidate surfaced for a human to confirm — the module never
# silently guesses on a restaurant's official domain.
_AUTO_VERIFY_CONFIDENCE_THRESHOLD = 0.75
_MEDIUM_CONFIDENCE_THRESHOLD = 0.5


def _confidence_level(score: float) -> ConfidenceLevel:
    if score >= _AUTO_VERIFY_CONFIDENCE_THRESHOLD:
        return ConfidenceLevel.HIGH
    if score >= _MEDIUM_CONFIDENCE_THRESHOLD:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


class SourceAuthorityService:
    def __init__(self, session: AsyncSession, provider: EntityResolutionProvider) -> None:
        self._session = session
        self._provider = provider
        self._sources = SourceRepository(session)

    async def resolve_official_website(self, query: EntityResolutionQuery) -> SourceAuthorityResult:
        candidates = await self._provider.resolve(query)

        if not candidates:
            return SourceAuthorityResult(
                restaurant_id=query.restaurant_id,
                status=ResolutionStatus.NOT_FOUND,
                reason="Provider returned no candidates",
            )

        # Best provider confidence first, so the top validated survivor is
        # the provider's own best guess, not an arbitrary surviving one.
        ranked = sorted(candidates, key=lambda c: c.provider_confidence, reverse=True)

        rejected: list[str] = []
        for candidate in ranked:
            accepted = self._try_accept(candidate, rejected)
            if accepted is None:
                continue
            normalized_url, confidence = accepted
            return await self._finalize(query, normalized_url, confidence, rejected)

        # Every candidate was disqualified.
        return SourceAuthorityResult(
            restaurant_id=query.restaurant_id,
            status=ResolutionStatus.REJECTED,
            rejected_candidates=rejected,
            reason="All candidates were rejected (aggregator or invalid domain)",
        )

    def _try_accept(
        self, candidate: EntityCandidate, rejected: list[str]
    ) -> tuple[str, ConfidenceLevel] | None:
        try:
            normalized_url, _domain_lock_config = validate_official_domain(candidate.url)
        except DomainRejectedError:
            rejected.append(candidate.url)
            return None
        return normalized_url, _confidence_level(candidate.provider_confidence)

    async def _finalize(
        self,
        query: EntityResolutionQuery,
        normalized_url: str,
        confidence: ConfidenceLevel,
        rejected: list[str],
    ) -> SourceAuthorityResult:
        if confidence == ConfidenceLevel.HIGH:
            record = await self._sources.create(
                restaurant_id=query.restaurant_id,
                source_type=SourceType.RESTAURANT_WEBSITE,
                url=normalized_url,
                is_verified_domain=True,
            )
            return SourceAuthorityResult(
                restaurant_id=query.restaurant_id,
                status=ResolutionStatus.VERIFIED,
                confidence=confidence,
                resolved_url=normalized_url,
                rejected_candidates=rejected,
                source_id=record.id,
            )

        return SourceAuthorityResult(
            restaurant_id=query.restaurant_id,
            status=ResolutionStatus.NEEDS_REVIEW,
            confidence=confidence,
            resolved_url=normalized_url,
            rejected_candidates=rejected,
            reason="Best candidate is below the auto-verify confidence threshold",
        )
