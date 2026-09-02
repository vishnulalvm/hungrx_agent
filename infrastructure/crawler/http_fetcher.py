"""httpx-based fetching for plain HTML and PDF documents — the default,
lightweight path used whenever a page doesn't require JS execution to
render its content. Every fetch goes through DomainVerifier (hard
allow-list), ssrf_guard (rejects private/loopback/link-local/reserved
resolved addresses — SSRF/cloud-metadata protection), and
RobotsChecker + DomainLock (politeness/rate-limiting) before a request
is made.
"""

from urllib.parse import urljoin

import httpx

from core.schemas.source import SnapshotContentType
from infrastructure.crawler.domain_lock import DomainLock, DomainVerifier, extract_domain
from infrastructure.crawler.fetch_result import FetchResult
from infrastructure.crawler.robots import RobotsChecker
from infrastructure.crawler.ssrf_guard import UnsafeHostError, assert_safe_host

# Same de-facto ceiling browsers use — a redirect chain longer than this
# is either a misconfiguration or a redirect loop, not a legitimate site.
_MAX_REDIRECTS = 10

# Buffered in memory in full (see fetch() below); caps a single response
# to a sane size for a restaurant menu/nutrition page so a malicious or
# misbehaving server can't exhaust worker memory with an oversized or
# slow-trickling response.
_MAX_RESPONSE_BYTES = 25 * 1024 * 1024


class RobotsDisallowedError(Exception):
    """Raised when robots.txt explicitly disallows fetching this URL."""


class ResponseTooLargeError(Exception):
    """Raised when a response exceeds _MAX_RESPONSE_BYTES."""


class DomainRejectedBySsrfGuardError(Exception):
    """Raised when a fetch target (initial URL or a followed redirect
    hop) resolves to a private/loopback/link-local/reserved address."""


class TooManyRedirectsError(Exception):
    """Raised when a fetch's redirect chain exceeds _MAX_REDIRECTS."""


def _classify_content_type(content_type_header: str | None) -> SnapshotContentType:
    header = (content_type_header or "").lower()
    if "pdf" in header:
        return SnapshotContentType.PDF
    return SnapshotContentType.HTML


class HttpFetcher:
    def __init__(
        self,
        *,
        domain_verifier: DomainVerifier,
        domain_lock: DomainLock,
        user_agent: str,
        timeout_seconds: float = 20.0,
        respect_robots: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """`transport` is a test-only seam (httpx.MockTransport) for
        exercising fetch()'s redirect/SSRF-guard/size-cap logic without a
        real network call; production callers never pass it."""
        self._domain_verifier = domain_verifier
        self._domain_lock = domain_lock
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._respect_robots = respect_robots
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            # Redirects are followed manually, one hop at a time, in
            # fetch() (below), via _validate_target — each hop is re-checked against
            # DomainVerifier and ssrf_guard before it's followed. httpx's
            # own follow_redirects=True would happily chase a redirect
            # straight to an internal/metadata address after only the
            # *original* URL had been validated (SSRF).
            follow_redirects=False,
            timeout=timeout_seconds,
            transport=transport,
        )
        self._robots_checkers: dict[str, RobotsChecker] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "HttpFetcher":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def _get_robots_checker(self, url: str) -> RobotsChecker:
        domain = self._domain_verifier.verified_domain
        checker = self._robots_checkers.get(domain)
        if checker is None:
            checker = RobotsChecker(url, self._user_agent, client=self._client)
            self._robots_checkers[domain] = checker
        return checker

    async def _validate_target(self, url: str) -> None:
        """The one check every hop of a fetch (initial request or a
        followed redirect) must pass before a connection is opened:
        inside the verified domain, and not a private/loopback/
        link-local/reserved resolved address."""
        self._domain_verifier.assert_allowed(url)
        try:
            await assert_safe_host(extract_domain(url))
        except UnsafeHostError as exc:
            raise DomainRejectedBySsrfGuardError(str(exc)) from exc

    async def fetch(self, url: str) -> FetchResult:
        """Fetches `url` as HTML or PDF (content type inferred from the
        response header). Raises DomainNotAllowedError if `url` (or any
        redirect hop along the way) is outside the verified domain
        (propagated from DomainVerifier.assert_allowed),
        DomainRejectedBySsrfGuardError if it resolves to a private/
        internal address, or RobotsDisallowedError if robots.txt
        disallows it. Redirects are followed manually (see __init__) so
        every hop gets the same validation as the original URL — a
        response is never fetched from an address that hasn't been
        checked."""
        await self._validate_target(url)

        if self._respect_robots:
            checker = await self._get_robots_checker(url)
            if not await checker.is_allowed(url):
                raise RobotsDisallowedError(f"robots.txt disallows fetching {url!r}")

        current_url = url
        for _ in range(_MAX_REDIRECTS + 1):
            async with self._domain_lock.throttled(current_url):
                async with self._client.stream("GET", current_url) as response:
                    if response.has_redirect_location:
                        next_url = urljoin(current_url, response.headers["location"])
                        await self._validate_target(next_url)
                        current_url = next_url
                        continue

                    content = await self._read_capped(response)
                    return FetchResult(
                        url=str(response.url),
                        content_type=_classify_content_type(response.headers.get("content-type")),
                        content=content,
                        http_status=response.status_code,
                        content_length_bytes=len(content),
                    )

        raise TooManyRedirectsError(f"Exceeded {_MAX_REDIRECTS} redirects fetching {url!r}")

    async def _read_capped(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length is not None and int(content_length) > _MAX_RESPONSE_BYTES:
            raise ResponseTooLargeError(
                f"Response Content-Length {content_length} exceeds cap of {_MAX_RESPONSE_BYTES} bytes"
            )

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _MAX_RESPONSE_BYTES:
                raise ResponseTooLargeError(
                    f"Response exceeded cap of {_MAX_RESPONSE_BYTES} bytes while streaming"
                )
            chunks.append(chunk)
        return b"".join(chunks)
