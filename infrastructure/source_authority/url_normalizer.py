"""Canonicalizes a candidate URL so equivalent addresses ("Example.com",
"https://example.com/", "http://www.example.com") compare and dedupe
consistently before validation/persistence.

Normalization applied:
  - default to https:// when no scheme is given
  - lowercase scheme and host
  - drop a default port (80 for http, 443 for https)
  - drop the fragment (#...)
  - strip common tracking query params (utm_*, gclid, fbclid) but keep
    everything else, since a real query param can be load-bearing for a
    site's routing
  - collapse an empty/root path to "/"
  - strip a trailing slash from any non-root path
"""

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from infrastructure.crawler.ssrf_guard import is_ip_literal_host

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset({"gclid", "fbclid", "msclkid"})


class InvalidUrlError(Exception):
    """Raised when a candidate string can't be normalized into a usable
    absolute URL (no discoverable hostname)."""


def _strip_tracking_params(query: str) -> str:
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key not in _TRACKING_PARAMS and not key.lower().startswith(_TRACKING_PARAM_PREFIXES)
    ]
    return urlencode(kept)


def normalize_url(raw_url: str) -> str:
    candidate = raw_url.strip()
    if not candidate:
        raise InvalidUrlError("URL is empty")

    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    if not parsed.hostname:
        raise InvalidUrlError(f"URL has no hostname: {raw_url!r}")
    if parsed.scheme not in ("http", "https"):
        raise InvalidUrlError(f"Unsupported URL scheme: {parsed.scheme!r}")

    host = parsed.hostname.lower()
    # Rejected here (before netloc reassembly, which would otherwise
    # silently drop an IPv6 literal's brackets and produce a
    # malformed/unparseable URL) rather than only at
    # domain_validator.py's IP-literal check downstream — a bare IP is
    # never a legitimate "official restaurant domain" either way, so
    # there's no case where normalize_url legitimately needs to succeed
    # on one.
    if is_ip_literal_host(host):
        raise InvalidUrlError(f"IP address literals are not accepted as a URL host: {host!r}")

    default_port = 443 if parsed.scheme == "https" else 80
    netloc = host if parsed.port in (None, default_port) else f"{host}:{parsed.port}"

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query = _strip_tracking_params(parsed.query)

    return urlunparse((parsed.scheme, netloc, path, parsed.params, query, ""))
