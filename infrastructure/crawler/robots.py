"""robots.txt fetching/parsing. Best-effort: a restaurant's marketing site
often has no robots.txt at all (or an unreachable one), and that must not
block a crawl the operator has already authorized by verifying the
domain — so a missing/unfetchable robots.txt is treated as "allow", while
an actual Disallow rule for our user agent is honored.
"""

import urllib.robotparser
from urllib.parse import urljoin

import httpx


class RobotsChecker:
    def __init__(self, base_url: str, user_agent: str, *, client: httpx.AsyncClient) -> None:
        self._robots_url = urljoin(base_url, "/robots.txt")
        self._user_agent = user_agent
        self._client = client
        self._parser: urllib.robotparser.RobotFileParser | None = None
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        parser = urllib.robotparser.RobotFileParser()
        try:
            # The shared client has follow_redirects=False (see
            # http_fetcher.py — redirects are validated per-hop against
            # the domain/SSRF guard elsewhere in that module); a
            # redirected robots.txt is treated the same as a missing one
            # rather than being followed or parsed as-is, since this
            # checker has no domain/SSRF validation of its own for a
            # redirect target.
            response = await self._client.get(self._robots_url, timeout=10.0)
            if response.status_code >= 400 or response.is_redirect:
                # No robots.txt (or inaccessible/redirected) — treat as
                # "allow all", matching how a browser/user would behave.
                parser.parse([])
                self._parser = parser
                return
            parser.parse(response.text.splitlines())
        except httpx.HTTPError:
            parser.parse([])

        self._parser = parser

    async def is_allowed(self, url: str) -> bool:
        await self._ensure_loaded()
        assert self._parser is not None
        return self._parser.can_fetch(self._user_agent, url)
