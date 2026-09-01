"""Domain restriction + per-domain concurrency/rate limiting.

Two separate concerns live here on purpose:

  - `DomainVerifier` — a hard allow-list check. The crawler must never
    fetch anything outside the restaurant's verified domain (e.g. a menu
    page that links out to a payment processor or social media site must
    not get crawled just because a link exists). This is a security
    boundary, not a courtesy.

  - `DomainLock` — a per-domain asyncio.Lock registry plus a minimum
    inter-request delay, so concurrent crawl tasks against the *same*
    domain serialize and space themselves out (politeness / basic
    rate-limiting), while different domains proceed independently.
"""

import asyncio
import time
from urllib.parse import urlparse


def extract_domain(url: str) -> str:
    """Returns the lowercased hostname of a URL, with a leading "www."
    stripped so "www.example.com" and "example.com" are treated as the
    same domain for locking/verification purposes."""
    hostname = urlparse(url).hostname
    if hostname is None:
        raise ValueError(f"URL has no hostname: {url!r}")
    hostname = hostname.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


class DomainNotAllowedError(Exception):
    """Raised when a crawl target falls outside the verified domain."""


class DomainVerifier:
    """Restricts crawling to a single verified restaurant domain.

    One instance per crawl job (a job is scoped to one restaurant's
    verified domain), not a global allow-list — a crawl for restaurant A
    must never be able to wander onto restaurant B's domain even if both
    are "verified" in the system generally.
    """

    def __init__(self, verified_domain: str) -> None:
        self._verified_domain = extract_domain(
            verified_domain if "://" in verified_domain else f"https://{verified_domain}"
        )

    @property
    def verified_domain(self) -> str:
        return self._verified_domain

    def is_allowed(self, url: str) -> bool:
        try:
            domain = extract_domain(url)
        except ValueError:
            return False
        return domain == self._verified_domain

    def assert_allowed(self, url: str) -> None:
        if not self.is_allowed(url):
            raise DomainNotAllowedError(
                f"URL {url!r} is outside the verified domain {self._verified_domain!r}"
            )


class DomainLock:
    """Serializes requests to the same domain and enforces a minimum delay
    between them. Safe to share across concurrent crawl tasks — locks and
    last-request timestamps are created lazily per domain and kept in this
    single instance's registry for the lifetime of the crawl job.
    """

    def __init__(self, min_interval_seconds: float = 1.0) -> None:
        self._min_interval_seconds = min_interval_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request_at: dict[str, float] = {}
        # Guards creation of a domain's lock/timestamp entries so two
        # concurrent tasks hitting a brand-new domain don't each create
        # their own separate asyncio.Lock (which would defeat locking).
        self._registry_guard = asyncio.Lock()

    async def _get_lock(self, domain: str) -> asyncio.Lock:
        async with self._registry_guard:
            lock = self._locks.get(domain)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[domain] = lock
            return lock

    async def acquire(self, url: str) -> None:
        """Blocks until it's this caller's turn to hit `url`'s domain,
        respecting both same-domain serialization and the minimum delay
        since the last request to that domain. Must be paired with
        `release` (or used via the `throttled` context manager below)."""
        domain = extract_domain(url)
        lock = await self._get_lock(domain)
        await lock.acquire()

        elapsed = time.monotonic() - self._last_request_at.get(domain, 0.0)
        remaining = self._min_interval_seconds - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

    def release(self, url: str) -> None:
        domain = extract_domain(url)
        self._last_request_at[domain] = time.monotonic()
        lock = self._locks.get(domain)
        if lock is not None and lock.locked():
            lock.release()

    def throttled(self, url: str) -> "_ThrottledRequest":
        return _ThrottledRequest(self, url)


class _ThrottledRequest:
    """`async with domain_lock.throttled(url): ...` — acquire on enter,
    release (and stamp the request time) on exit, success or failure."""

    def __init__(self, domain_lock: DomainLock, url: str) -> None:
        self._domain_lock = domain_lock
        self._url = url

    async def __aenter__(self) -> None:
        await self._domain_lock.acquire(self._url)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._domain_lock.release(self._url)
