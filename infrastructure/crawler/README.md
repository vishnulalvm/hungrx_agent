# infrastructure/crawler/

Fetches and stores content from a restaurant's *verified* website only.
No AI extraction happens here — this module's job ends at "here is a
hashed, stored snapshot of a page."

## Domain restriction (read this first)

`domain_lock.py`:
- `extract_domain(url)` — normalizes a URL to a bare host for comparison
  (strips `www.`).
- `DomainVerifier` — host-exact match; deliberately does **not** allow
  subdomains or lookalike domains to pass as the verified domain.
- `DomainLock` — per-domain `asyncio.Lock` registry plus a minimum
  inter-request interval, exposed via an async `throttled()` context
  manager. Every fetch through `HttpFetcher`/`BrowserFetcher` goes through
  this, so concurrent crawls of the same domain are serialized and
  rate-limited automatically.

This same domain-lock logic is reused (not duplicated) by
`infrastructure/source_authority/domain_validator.py` — if you change the
matching rules here, check that module too.

`ssrf_guard.py` — the other half of domain restriction, layered on top
of `DomainVerifier`'s hostname allow-list: `assert_safe_host(hostname)`
resolves the hostname and rejects it if any resolved address is
private/loopback/link-local/reserved (RFC 1918, `169.254.169.254` cloud
metadata, `127.0.0.1`, `::1`, etc.), and `is_ip_literal_host(hostname)`
rejects a bare IP address as a host outright without needing DNS. A
hostname passing `DomainVerifier` can still resolve internally — either
because the "official" URL was an IP literal in the first place, or via
DNS rebinding (public IP at verification time, internal one at
connection time) — so `assert_safe_host` is checked independently at
actual connection time in `HttpFetcher.fetch()` (every request *and*
every followed redirect hop), not just once at domain-verification time.

## Fetching

- `http_fetcher.py` — `HttpFetcher`: default fetch path, httpx-based,
  domain-verified, SSRF-guarded, robots-checked, throttled. Use this
  unless you specifically need JS rendering. `follow_redirects=False` on
  the underlying client deliberately — redirects are followed manually,
  one hop at a time (capped at 10), with `DomainVerifier`/`ssrf_guard`
  re-checked before each hop is followed; httpx's own
  `follow_redirects=True` would fetch a redirect target with zero
  re-validation, which is exactly the gap a same-domain-verified site
  redirecting to an internal address would exploit. Responses are capped
  at 25MB (`Content-Length` pre-check plus a running counter while
  streaming) against memory-exhaustion from an oversized or
  slow-trickling response.
- `browser_fetcher.py` — `BrowserFetcher`: Playwright-based, used only
  when a page requires JS rendering (`fetch_rendered_html`) or for
  screenshot capture (`capture_screenshot`). Not the default path —
  browser automation is comparatively expensive. Only checks
  `DomainVerifier` against the initial URL; Playwright follows redirects
  internally with no per-hop domain/SSRF re-validation hook wired up
  here (unlike `HttpFetcher`) — a known gap, not yet closed, since this
  isn't the default fetch path.
- `robots.py` — `RobotsChecker`: best-effort; a missing or unreachable
  `robots.txt` is treated as "allow," not "deny."
- `fetch_result.py` — `FetchResult` dataclass: url, content_type,
  content, http_status, content_length_bytes. Common return shape for
  both fetchers.

## Content processing

- `hashing.py` — `sha256_hex(content: bytes) -> str`. Used to detect
  whether a re-crawled page actually changed.
- `metadata.py` — `extract_page_metadata(html) -> PageMetadata` (title,
  description, canonical_url, og_title/description/image) via
  BeautifulSoup.

## Storage

- `snapshot_service.py` — `SnapshotService.store_snapshot(source_id,
  result) -> SourceSnapshot`: hashes the fetched content, persists it via
  a `StorageAdapter` (see `infrastructure/storage/`), and returns a typed
  `SourceSnapshot` schema.
- `crawler_service.py` — `CrawlerService`: the top-level entry point
  other code should call (`fetch_and_store`, `capture_screenshot`) rather
  than reaching for `HttpFetcher`/`BrowserFetcher` directly.

## Page discovery

`page_discovery.py` — `find_menu_page_links(html, base_url,
domain_verifier)`: deterministic, keyword-based (not AI) link filtering
to find menu/nutrition-relevant pages linked from an already-fetched
HTML page. Only looks at link text/href, never page content — this is
what the collector workflow's Extraction node
(`workflows/collector_workflow/nodes/extraction.py`) uses to decide
which additional pages to crawl beyond the source root, while staying on
the "raw capture" side of the raw-extraction/AI-interpretation boundary.

## Supports both HTML and PDF

Content type is carried on `FetchResult`/`SourceSnapshot`
(`SnapshotContentType` in `core/schemas/source.py`); there's no separate
PDF-specific fetcher — the same `HttpFetcher` handles both, since the
distinction is just content-type/handling downstream, not the fetch
mechanism itself.
