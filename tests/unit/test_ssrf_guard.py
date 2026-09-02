"""Unit tests for infrastructure.crawler.ssrf_guard — the private/
loopback/link-local/reserved address check used both at "official
domain" validation time (domain_validator.py, url_normalizer.py) and at
crawler connection time (http_fetcher.py)."""

import pytest

from infrastructure.crawler.ssrf_guard import (
    UnsafeHostError,
    assert_safe_host,
    is_ip_literal_host,
)


class TestIsIpLiteralHost:
    def test_ipv4_literal_is_detected(self) -> None:
        assert is_ip_literal_host("169.254.169.254") is True

    def test_ipv6_literal_is_detected(self) -> None:
        assert is_ip_literal_host("::1") is True

    def test_hostname_is_not_a_literal(self) -> None:
        assert is_ip_literal_host("example.com") is False


@pytest.mark.asyncio
class TestAssertSafeHost:
    async def test_raises_for_a_private_ip_literal(self) -> None:
        with pytest.raises(UnsafeHostError):
            await assert_safe_host("10.0.0.5")

    async def test_raises_for_the_cloud_metadata_address(self) -> None:
        with pytest.raises(UnsafeHostError):
            await assert_safe_host("169.254.169.254")

    async def test_raises_for_loopback(self) -> None:
        with pytest.raises(UnsafeHostError):
            await assert_safe_host("127.0.0.1")

    async def test_raises_for_ipv6_loopback(self) -> None:
        with pytest.raises(UnsafeHostError):
            await assert_safe_host("::1")

    async def test_allows_a_public_ip_literal(self) -> None:
        await assert_safe_host("93.184.216.34")  # example.com's real IP; no exception

    async def test_raises_when_hostname_resolves_privately(self, monkeypatch) -> None:
        import infrastructure.crawler.ssrf_guard as module

        def fake_resolve(hostname: str):
            return [(2, 1, 6, "", ("127.0.0.1", 0))]

        monkeypatch.setattr(module, "_resolve", fake_resolve)
        with pytest.raises(UnsafeHostError):
            await assert_safe_host("looks-public-but-resolves-internal.example")

    async def test_allows_hostname_resolving_to_a_public_address(self, monkeypatch) -> None:
        import infrastructure.crawler.ssrf_guard as module

        def fake_resolve(hostname: str):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(module, "_resolve", fake_resolve)
        await assert_safe_host("example.com")

    async def test_raises_when_resolution_fails(self, monkeypatch) -> None:
        import socket

        import infrastructure.crawler.ssrf_guard as module

        def fake_resolve(hostname: str):
            raise socket.gaierror("no such host")

        monkeypatch.setattr(module, "_resolve", fake_resolve)
        with pytest.raises(UnsafeHostError):
            await assert_safe_host("does-not-resolve.example")
