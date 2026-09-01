"""Validates a normalized candidate URL as a plausible *official* domain
and, once accepted, builds the DomainVerifier/DomainLock configuration the
crawler will later be restricted to for this restaurant.
"""

from dataclasses import dataclass

from infrastructure.crawler.domain_lock import DomainVerifier, extract_domain
from infrastructure.source_authority.aggregator_blocklist import is_known_aggregator
from infrastructure.source_authority.url_normalizer import InvalidUrlError, normalize_url


class DomainRejectedError(Exception):
    """Raised with a human-readable reason when a candidate URL fails
    official-domain validation (aggregator, malformed, disallowed
    scheme, ...)."""


@dataclass(frozen=True, slots=True)
class DomainLockConfig:
    """The domain-scoping configuration the crawler must be constructed
    with once a restaurant's official domain is verified — kept as a
    plain, serializable value here (not a live DomainVerifier instance)
    so it can be persisted/logged; the crawler builds its own
    DomainVerifier from `verified_domain` when a crawl job actually
    starts."""

    verified_domain: str
    allowed_url: str


def validate_official_domain(raw_url: str) -> tuple[str, DomainLockConfig]:
    """Normalizes `raw_url` and validates it as an acceptable official
    domain. Returns (normalized_url, DomainLockConfig) on success; raises
    DomainRejectedError with the reason on failure."""
    try:
        normalized = normalize_url(raw_url)
    except InvalidUrlError as exc:
        raise DomainRejectedError(str(exc)) from exc

    domain = extract_domain(normalized)

    if is_known_aggregator(domain):
        raise DomainRejectedError(f"{domain!r} is a known aggregator/third-party domain")

    # Sanity-check the domain shape itself (DomainVerifier would accept
    # any non-empty hostname; this catches obviously-malformed hosts like
    # a bare "localhost" or an IP literal slipping through as "official").
    if "." not in domain:
        raise DomainRejectedError(f"{domain!r} is not a valid public domain")

    # Round-trips through DomainVerifier to confirm the normalized URL is
    # actually self-consistent with the domain we just extracted from it.
    verifier = DomainVerifier(domain)
    if not verifier.is_allowed(normalized):
        raise DomainRejectedError(f"{normalized!r} does not resolve back to domain {domain!r}")

    return normalized, DomainLockConfig(verified_domain=domain, allowed_url=normalized)
