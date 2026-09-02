"""SSRF guard: rejects hostnames/addresses that resolve into a private,
loopback, link-local, multicast, unspecified, or otherwise reserved IP
range — most importantly the cloud metadata address (169.254.169.254)
and the RFC 1918 ranges.

Domain-allow-listing (DomainVerifier) restricts *which hostname* a crawl
targets, but a hostname passing that check can still resolve to an
internal address — either because the "official" URL itself was an IP
literal, or because the DNS name legitimately/maliciously resolves
internally (DNS rebinding: the name could resolve to a public IP at
verification time and an internal one at connection time). This module
is checked both when a candidate official domain is first validated
(infrastructure/source_authority/domain_validator.py) and at actual
connection time for every request the crawler makes (http_fetcher.py),
since only the latter is immune to DNS rebinding.
"""

import asyncio
import ipaddress
import socket


class UnsafeHostError(Exception):
    """Raised when a hostname is (or resolves to) a private/loopback/
    link-local/reserved address — never a safe crawl target."""


def _is_unsafe_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_ip_literal_host(hostname: str) -> bool:
    """True if `hostname` parses as an IP address literal at all (private
    or public) — used to reject an IP literal outright as a candidate
    "official restaurant domain" (infrastructure/source_authority/
    domain_validator.py) without needing DNS resolution: a real
    restaurant's official site is never legitimately identified by bare
    IP address, so there's no false-positive risk in rejecting all of
    them, not just the private ones."""
    try:
        ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    return True


def _resolve(hostname: str) -> list[tuple]:
    return socket.getaddrinfo(hostname, None)


async def assert_safe_host(hostname: str) -> None:
    """Raises UnsafeHostError if `hostname` is itself a private/reserved
    IP literal, or resolves (via the same DNS resolution the actual
    connection will use) to one. Every resolved address is checked, not
    just the first, since a hostname can have multiple A/AAAA records.
    Runs the (blocking) resolver call in a thread so this can be awaited
    from the crawler's async request path without stalling the event
    loop."""
    if _is_unsafe_address(hostname):
        raise UnsafeHostError(f"{hostname!r} is a private/reserved IP address")

    try:
        addrinfo = await asyncio.to_thread(_resolve, hostname)
    except socket.gaierror as exc:
        raise UnsafeHostError(f"Could not resolve {hostname!r}: {exc}") from exc

    for _family, _type, _proto, _canonname, sockaddr in addrinfo:
        resolved_address = sockaddr[0]
        if _is_unsafe_address(resolved_address):
            raise UnsafeHostError(
                f"{hostname!r} resolves to {resolved_address!r}, a private/reserved IP address"
            )
