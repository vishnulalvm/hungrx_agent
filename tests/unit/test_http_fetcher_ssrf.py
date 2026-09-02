"""Unit tests for HttpFetcher's SSRF-hardening behavior — the core of
this platform's security review fix for the crawler's redirect-following
gap. Uses httpx.MockTransport (no real network) to script server
responses, including redirects to internal addresses, oversized bodies,
and redirect loops.

DomainVerifier is constructed with "example.com" throughout; a redirect
to a different host is what proves _validate_target actually re-runs on
every hop, not just the initial URL. assert_safe_host's DNS resolution
is monkeypatched to a fixed set of hostname->address mappings so these
tests don't depend on real DNS.
"""

import httpx
import pytest

import infrastructure.crawler.ssrf_guard as ssrf_guard
from infrastructure.crawler.domain_lock import DomainLock, DomainVerifier
from infrastructure.crawler.http_fetcher import (
    DomainRejectedBySsrfGuardError,
    HttpFetcher,
    ResponseTooLargeError,
    TooManyRedirectsError,
)
from infrastructure.crawler.domain_lock import DomainNotAllowedError

pytestmark = pytest.mark.asyncio


def _resolve_map(mapping: dict[str, str]):
    def fake_resolve(hostname: str):
        address = mapping.get(hostname, "93.184.216.34")  # a generic public IP fallback
        return [(2, 1, 6, "", (address, 0))]

    return fake_resolve


def _fetcher(handler, *, monkeypatch, resolve_map: dict[str, str] | None = None) -> HttpFetcher:
    if resolve_map is not None:
        monkeypatch.setattr(ssrf_guard, "_resolve", _resolve_map(resolve_map))
    return HttpFetcher(
        domain_verifier=DomainVerifier("example.com"),
        domain_lock=DomainLock(min_interval_seconds=0.0),
        user_agent="test-agent",
        respect_robots=False,
        transport=httpx.MockTransport(handler),
    )


class TestBasicFetch:
    async def test_fetches_html_from_the_verified_domain(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>hi</html>", headers={"content-type": "text/html"})

        fetcher = _fetcher(handler, monkeypatch=monkeypatch, resolve_map={"example.com": "93.184.216.34"})
        result = await fetcher.fetch("https://example.com/menu")

        assert result.content == b"<html>hi</html>"
        assert result.http_status == 200

    async def test_rejects_url_outside_verified_domain_without_any_request(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should never reach the network for an out-of-domain URL")

        fetcher = _fetcher(handler, monkeypatch=monkeypatch)
        with pytest.raises(DomainNotAllowedError):
            await fetcher.fetch("https://not-example.com/menu")


class TestRedirectRevalidation:
    async def test_follows_a_same_domain_redirect(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(302, headers={"location": "https://example.com/new"})
            return httpx.Response(200, content=b"final", headers={"content-type": "text/html"})

        fetcher = _fetcher(handler, monkeypatch=monkeypatch, resolve_map={"example.com": "93.184.216.34"})
        result = await fetcher.fetch("https://example.com/old")

        assert result.content == b"final"

    async def test_rejects_a_redirect_to_a_different_domain(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "https://attacker.com/steal"})
            raise AssertionError("must never follow the redirect off-domain")

        fetcher = _fetcher(handler, monkeypatch=monkeypatch, resolve_map={"example.com": "93.184.216.34"})
        with pytest.raises(DomainNotAllowedError):
            await fetcher.fetch("https://example.com/redirect-away")

    async def test_rejects_a_redirect_to_a_private_ip_address(self, monkeypatch) -> None:
        # example.com's own DNS is fine, but it redirects to a hostname
        # that resolves to the cloud metadata address — this is the
        # SSRF scenario the fix specifically closes.
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(
                    302, headers={"location": "https://internal.example.com/secret"}
                )
            raise AssertionError("must never actually connect to the internal target")

        fetcher = _fetcher(
            handler,
            monkeypatch=monkeypatch,
            resolve_map={"example.com": "93.184.216.34", "internal.example.com": "169.254.169.254"},
        )
        # internal.example.com is also outside the verified domain
        # (example.com exact-match only), so this is caught by
        # DomainVerifier first — proving defense in depth. See the next
        # test for the SSRF guard catching a same-domain-but-private case.
        with pytest.raises(DomainNotAllowedError):
            await fetcher.fetch("https://example.com/redirect-to-internal")

    async def test_rejects_when_the_verified_domain_itself_resolves_privately(self, monkeypatch) -> None:
        # A same-domain redirect can't be caught by DomainVerifier (the
        # host matches) — only the SSRF guard's resolved-address check
        # catches this. Simulates DNS rebinding: the verified domain
        # itself now resolves to a private address.
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must never connect once the resolved address is private")

        fetcher = _fetcher(handler, monkeypatch=monkeypatch, resolve_map={"example.com": "10.0.0.5"})
        with pytest.raises(DomainRejectedBySsrfGuardError):
            await fetcher.fetch("https://example.com/menu")

    async def test_redirect_loop_raises_too_many_redirects(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://example.com/loop"})

        fetcher = _fetcher(handler, monkeypatch=monkeypatch, resolve_map={"example.com": "93.184.216.34"})
        with pytest.raises(TooManyRedirectsError):
            await fetcher.fetch("https://example.com/loop")


class TestResponseSizeCap:
    async def test_rejects_oversized_content_length(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html", "content-length": str(100 * 1024 * 1024)},
                content=b"x" * 10,
            )

        fetcher = _fetcher(handler, monkeypatch=monkeypatch, resolve_map={"example.com": "93.184.216.34"})
        with pytest.raises(ResponseTooLargeError):
            await fetcher.fetch("https://example.com/huge")

    async def test_rejects_oversized_streamed_body_with_no_content_length(self, monkeypatch) -> None:
        # No Content-Length header — must still be caught while streaming.
        oversized = b"x" * (26 * 1024 * 1024)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/html"}, content=oversized)

        fetcher = _fetcher(handler, monkeypatch=monkeypatch, resolve_map={"example.com": "93.184.216.34"})
        with pytest.raises(ResponseTooLargeError):
            await fetcher.fetch("https://example.com/huge-streamed")

    async def test_accepts_content_under_the_cap(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"normal page")

        fetcher = _fetcher(handler, monkeypatch=monkeypatch, resolve_map={"example.com": "93.184.216.34"})
        result = await fetcher.fetch("https://example.com/normal")
        assert result.content == b"normal page"
