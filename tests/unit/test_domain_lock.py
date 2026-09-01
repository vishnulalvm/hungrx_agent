"""Unit tests for domain verification and per-domain locking/rate
limiting — pure asyncio, no network calls."""

import asyncio
import time

import pytest

from infrastructure.crawler.domain_lock import (
    DomainLock,
    DomainNotAllowedError,
    DomainVerifier,
    extract_domain,
)


class TestExtractDomain:
    def test_extracts_hostname(self) -> None:
        assert extract_domain("https://example.com/menu") == "example.com"

    def test_strips_www_prefix(self) -> None:
        assert extract_domain("https://www.example.com/menu") == "example.com"

    def test_lowercases_hostname(self) -> None:
        assert extract_domain("https://EXAMPLE.com/menu") == "example.com"

    def test_ignores_port(self) -> None:
        assert extract_domain("https://example.com:8443/menu") == "example.com"

    def test_raises_for_url_with_no_hostname(self) -> None:
        with pytest.raises(ValueError):
            extract_domain("not-a-url")


class TestDomainVerifier:
    def test_allows_url_on_verified_domain(self) -> None:
        verifier = DomainVerifier("example.com")
        assert verifier.is_allowed("https://example.com/menu")

    def test_allows_www_variant_of_verified_domain(self) -> None:
        verifier = DomainVerifier("example.com")
        assert verifier.is_allowed("https://www.example.com/menu")

    def test_verified_domain_can_itself_include_www(self) -> None:
        verifier = DomainVerifier("www.example.com")
        assert verifier.verified_domain == "example.com"
        assert verifier.is_allowed("https://example.com/menu")

    def test_rejects_url_on_different_domain(self) -> None:
        verifier = DomainVerifier("example.com")
        assert not verifier.is_allowed("https://evil.com/menu")

    def test_rejects_subdomain_not_equal_to_verified_domain(self) -> None:
        # "shop.example.com" is not the same host as "example.com" — the
        # crawler should not silently widen scope to arbitrary subdomains.
        verifier = DomainVerifier("example.com")
        assert not verifier.is_allowed("https://shop.example.com/menu")

    def test_rejects_lookalike_domain(self) -> None:
        verifier = DomainVerifier("example.com")
        assert not verifier.is_allowed("https://example.com.evil.com/menu")

    def test_assert_allowed_raises_for_disallowed_url(self) -> None:
        verifier = DomainVerifier("example.com")
        with pytest.raises(DomainNotAllowedError):
            verifier.assert_allowed("https://evil.com/menu")

    def test_assert_allowed_is_silent_for_allowed_url(self) -> None:
        verifier = DomainVerifier("example.com")
        verifier.assert_allowed("https://example.com/menu")  # no raise

    def test_malformed_url_is_not_allowed(self) -> None:
        verifier = DomainVerifier("example.com")
        assert not verifier.is_allowed("not-a-url")


@pytest.mark.asyncio
class TestDomainLockSerialization:
    async def test_same_domain_requests_are_serialized(self) -> None:
        lock = DomainLock(min_interval_seconds=0.0)
        active = 0
        max_concurrent = 0

        async def hit(url: str) -> None:
            nonlocal active, max_concurrent
            async with lock.throttled(url):
                active += 1
                max_concurrent = max(max_concurrent, active)
                await asyncio.sleep(0.05)
                active -= 1

        await asyncio.gather(
            hit("https://example.com/a"),
            hit("https://example.com/b"),
            hit("https://www.example.com/c"),  # same domain after www-stripping
        )

        assert max_concurrent == 1

    async def test_different_domains_run_concurrently(self) -> None:
        lock = DomainLock(min_interval_seconds=0.0)
        active = 0
        max_concurrent = 0

        async def hit(url: str) -> None:
            nonlocal active, max_concurrent
            async with lock.throttled(url):
                active += 1
                max_concurrent = max(max_concurrent, active)
                await asyncio.sleep(0.05)
                active -= 1

        await asyncio.gather(
            hit("https://one.com/a"),
            hit("https://two.com/a"),
            hit("https://three.com/a"),
        )

        assert max_concurrent == 3

    async def test_enforces_minimum_interval_between_requests(self) -> None:
        lock = DomainLock(min_interval_seconds=0.1)

        start = time.monotonic()
        async with lock.throttled("https://example.com/a"):
            pass
        async with lock.throttled("https://example.com/b"):
            pass
        elapsed = time.monotonic() - start

        assert elapsed >= 0.1

    async def test_release_is_safe_to_call_after_context_manager_exit(self) -> None:
        lock = DomainLock(min_interval_seconds=0.0)
        async with lock.throttled("https://example.com/a"):
            pass
        # A second acquire/release cycle on the same domain must not hang
        # or raise — proves the lock was actually released on exit.
        async with lock.throttled("https://example.com/a"):
            pass
