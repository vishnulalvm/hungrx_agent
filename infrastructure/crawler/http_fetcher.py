"""httpx-based fetching for plain HTML and PDF documents — the default,
lightweight path used whenever a page doesn't require JS execution to
render its content. Every fetch goes through DomainVerifier (hard
allow-list), ssrf_guard (rejects private/loopback/link-local/reserved
resolved addresses — SSRF/cloud-metadata protection), and
RobotsChecker + DomainLock (politeness/rate-limiting) before a request
is made.

When `flaresolverr_url` is configured (see core/config/settings.py), a
direct response that looks like a Cloudflare JS challenge (403/503 plus
a Cloudflare marker — see `_looks_like_cloudflare_challenge`) is retried
exactly once through FlareSolverr instead of being returned/raised as a
failure. FlareSolverr runs its own headless browser to solve the
challenge and hands back the resulting HTML — it does its own outbound
fetch from inside its own container, so this is only ever used for a
URL that already passed `_validate_target` (domain-verified,
SSRF-checked) on this call.
"""

from urllib.parse import urljoin

import httpx

from core.schemas.source import SnapshotContentType
from infrastructure.crawler.domain_lock import DomainLock, DomainVerifier, extract_domain
from infrastructure.crawler.fetch_result import FetchResult
from infrastructure.crawler.robots import RobotsChecker
from infrastructure.crawler.ssrf_guard import UnsafeHostError, assert_safe_host

# Status codes Cloudflare (and similar WAFs) use for a JS/browser
# challenge page rather than a real "forbidden"/"unavailable" response.
_CHALLENGE_STATUS_CODES = {403, 503}

# Cheap, low-false-positive markers that the *content itself* is a
# challenge page — checked in addition to status code so a site's own
# genuine 403/503 (e.g. real access-denied, real maintenance page) isn't
# mistaken for a solvable challenge and silently retried.
_CLOUDFLARE_BODY_MARKERS = (
    b"cf-browser-verification",
    b"cf_chl_",
    b"Checking your browser before accessing",
    b"Attention Required! | Cloudflare",
    b"Just a moment...",
)

# FlareSolverr's default request timeout for solving a challenge is
# generous (its own internal default is 60s); this is our client-side
# ceiling on top of that.
_FLARESOLVERR_TIMEOUT_SECONDS = 65.0


def _looks_like_cloudflare_challenge(*, status_code: int, headers: httpx.Headers, body: bytes) -> bool:
    if status_code not in _CHALLENGE_STATUS_CODES:
        return False
    if "cloudflare" in headers.get("server", "").lower():
        return True
    if headers.get("cf-mitigated") is not None:
        return True
    return any(marker in body for marker in _CLOUDFLARE_BODY_MARKERS)


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
        flaresolverr_url: str | None = None,
        flaresolverr_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """`transport` is a test-only seam (httpx.MockTransport) for
        exercising fetch()'s redirect/SSRF-guard/size-cap logic without a
        real network call; production callers never pass it.
        `flaresolverr_url` enables the Cloudflare-challenge fallback (see
        module docstring) when set; `flaresolverr_transport` is the same
        kind of test-only seam as `transport`, for the separate client
        used to call FlareSolverr."""
        self._domain_verifier = domain_verifier
        self._domain_lock = domain_lock
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._respect_robots = respect_robots
        self._flaresolverr_url = flaresolverr_url.rstrip("/") if flaresolverr_url else None
        self._flaresolverr_client = (
            httpx.AsyncClient(timeout=_FLARESOLVERR_TIMEOUT_SECONDS, transport=flaresolverr_transport)
            if self._flaresolverr_url
            else None
        )
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
        if self._flaresolverr_client is not None:
            await self._flaresolverr_client.aclose()

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

                    if self._flaresolverr_client is not None and _looks_like_cloudflare_challenge(
                        status_code=response.status_code, headers=response.headers, body=content
                    ):
                        solved = await self._fetch_via_flaresolverr(current_url)
                        if solved is not None:
                            return solved

                    return FetchResult(
                        url=str(response.url),
                        content_type=_classify_content_type(response.headers.get("content-type")),
                        content=content,
                        http_status=response.status_code,
                        content_length_bytes=len(content),
                    )

        raise TooManyRedirectsError(f"Exceeded {_MAX_REDIRECTS} redirects fetching {url!r}")

    async def _fetch_via_flaresolverr(self, url: str) -> FetchResult | None:
        """Asks FlareSolverr to solve the challenge for `url` and returns
        the resulting page as a FetchResult, or None if FlareSolverr
        itself failed to solve it (caller then falls back to the
        original direct-fetch response rather than treating this as a
        hard failure — FlareSolverr is a best-effort fallback)."""
        assert self._flaresolverr_client is not None and self._flaresolverr_url is not None

        try:
            response = await self._flaresolverr_client.post(
                f"{self._flaresolverr_url}/v1",
                json={
                    "cmd": "request.get",
                    "url": url,
                    "maxTimeout": int(_FLARESOLVERR_TIMEOUT_SECONDS * 1000),
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        if payload.get("status") != "ok":
            return None

        solution = payload.get("solution") or {}
        html = solution.get("response")
        if not isinstance(html, str):
            return None

        content = html.encode("utf-8")
        return FetchResult(
            url=solution.get("url", url),
            content_type=SnapshotContentType.HTML,
            content=content,
            http_status=solution.get("status", 200),
            content_length_bytes=len(content),
        )

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
