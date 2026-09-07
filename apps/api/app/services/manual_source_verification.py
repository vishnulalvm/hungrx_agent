"""Verifies and persists a Source record for an admin-supplied,
already-known official URL — no entity-resolution search involved.

This is the "skip search, still validate" path for the manual/verified
ingestion queue (see database/repositories/ingestion_queue_repository.py):
the admin already knows the restaurant's official website, so there's
nothing to search for, but the URL still has to clear the same
aggregator-blocklist/malformed-domain/IP-literal checks a search-based
candidate would — reusing infrastructure/source_authority/
domain_validator.py's validate_official_domain directly rather than
reimplementing any of that. Persistence reuses SourceRepository.create,
the same single write path apps/api/app/services/source_authority_service.py's
_finalize already uses for an auto-verified search result.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.source import SourceType
from database.models.source import Source
from database.repositories.source_repository import SourceRepository
from infrastructure.source_authority.domain_validator import DomainRejectedError, validate_official_domain


class ManualSourceRejectedError(Exception):
    """Raised when an admin-supplied official URL fails validation (known
    aggregator, malformed domain, IP literal, etc.) — the same reasons
    validate_official_domain would reject a search-based candidate."""


async def verify_and_persist_manual_source(
    session: AsyncSession, *, restaurant_id: uuid.UUID, raw_url: str
) -> Source:
    try:
        normalized_url, _domain_lock_config = validate_official_domain(raw_url)
    except DomainRejectedError as exc:
        raise ManualSourceRejectedError(str(exc)) from exc

    return await SourceRepository(session).create(
        restaurant_id=restaurant_id,
        source_type=SourceType.RESTAURANT_WEBSITE,
        url=normalized_url,
        is_verified_domain=True,
    )
