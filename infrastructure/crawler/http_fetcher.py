"""httpx-based fetching for plain HTML and PDF documents — the default,
lightweight path used whenever a page doesn't require JS execution to
render its content. Every fetch goes through DomainVerifier (hard
allow-list) and RobotsChecker + DomainLock (politeness/rate-limiting)
before a request is made.
"""

import httpx

from core.schemas.source import SnapshotContentType
from infrastructure.crawler.domain_lock import DomainLock, DomainVerifier
from infrastructure.crawler.fetch_result import FetchResult
from infrastructure.crawler.robots import RobotsChecker


class RobotsDisallowedError(Exception):
    """Raised when robots.txt explicitly disallows fetching this URL."""


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
    ) -> None:
        self._domain_verifier = domain_verifier
        self._domain_lock = domain_lock
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._respect_robots = respect_robots
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            timeout=timeout_seconds,
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

    async def fetch(self, url: str) -> FetchResult:
        """Fetches `url` as HTML or PDF (content type inferred from the
        response header). Raises DomainNotAllowedError if `url` is outside
        the verified domain (propagated from DomainVerifier.assert_allowed)
        or RobotsDisallowedError if robots.txt disallows it."""
        self._domain_verifier.assert_allowed(url)

        if self._respect_robots:
            checker = await self._get_robots_checker(url)
            if not await checker.is_allowed(url):
                raise RobotsDisallowedError(f"robots.txt disallows fetching {url!r}")

        async with self._domain_lock.throttled(url):
            response = await self._client.get(url)

        content = response.content
        return FetchResult(
            url=str(response.url),
            content_type=_classify_content_type(response.headers.get("content-type")),
            content=content,
            http_status=response.status_code,
            content_length_bytes=len(content),
        )
