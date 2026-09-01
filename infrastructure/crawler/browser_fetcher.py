"""Playwright-based fetching — used only when a page needs JS execution to
render its real content (client-side-rendered menus, etc.) or when a
screenshot is explicitly requested. This is deliberately not the default
path: HttpFetcher (httpx) is cheaper and sufficient for the common case of
static/server-rendered HTML and PDFs, so callers should only reach for
BrowserFetcher when they know they need it.
"""

from playwright.async_api import Browser, async_playwright

from core.schemas.source import SnapshotContentType
from infrastructure.crawler.domain_lock import DomainLock, DomainVerifier
from infrastructure.crawler.fetch_result import FetchResult


class BrowserFetcher:
    def __init__(
        self,
        *,
        domain_verifier: DomainVerifier,
        domain_lock: DomainLock,
        user_agent: str,
        headless: bool = True,
        navigation_timeout_ms: int = 30_000,
    ) -> None:
        self._domain_verifier = domain_verifier
        self._domain_lock = domain_lock
        self._user_agent = user_agent
        self._headless = headless
        self._navigation_timeout_ms = navigation_timeout_ms
        self._playwright = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> "BrowserFetcher":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def fetch_rendered_html(self, url: str) -> FetchResult:
        """Navigates to `url`, waits for the network to settle, and
        returns the fully rendered DOM as HTML."""
        self._domain_verifier.assert_allowed(url)
        assert self._browser is not None, "BrowserFetcher must be used as an async context manager"

        async with self._domain_lock.throttled(url):
            page = await self._browser.new_page(user_agent=self._user_agent)
            try:
                page.set_default_navigation_timeout(self._navigation_timeout_ms)
                response = await page.goto(url, wait_until="networkidle")
                html = await page.content()
                status = response.status if response is not None else None
            finally:
                await page.close()

        content = html.encode("utf-8")
        return FetchResult(
            url=url,
            content_type=SnapshotContentType.HTML,
            content=content,
            http_status=status,
            content_length_bytes=len(content),
        )

    async def capture_screenshot(self, url: str, *, full_page: bool = True) -> FetchResult:
        """Navigates to `url` and returns a PNG screenshot of the rendered
        page."""
        self._domain_verifier.assert_allowed(url)
        assert self._browser is not None, "BrowserFetcher must be used as an async context manager"

        async with self._domain_lock.throttled(url):
            page = await self._browser.new_page(user_agent=self._user_agent)
            try:
                page.set_default_navigation_timeout(self._navigation_timeout_ms)
                response = await page.goto(url, wait_until="networkidle")
                screenshot = await page.screenshot(full_page=full_page, type="png")
                status = response.status if response is not None else None
            finally:
                await page.close()

        return FetchResult(
            url=url,
            content_type=SnapshotContentType.SCREENSHOT,
            content=screenshot,
            http_status=status,
            content_length_bytes=len(screenshot),
        )
