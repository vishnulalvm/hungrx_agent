"""Deterministic (keyword-based, not AI) discovery of menu/nutrition-
relevant pages linked from an already-fetched HTML page. This is purely
structural link filtering — it never reads or interprets the *content*
of a candidate page, only its URL/link text — so it stays firmly on the
"raw capture" side of the raw-extraction/AI-interpretation boundary the
Extraction node has to respect.
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from infrastructure.crawler.domain_lock import DomainVerifier

# Deliberately narrow and food-industry-specific so this doesn't just
# match every restaurant page ("about", "contact", ...). Matched
# case-insensitively against both the link text and the URL path.
MENU_KEYWORDS: frozenset[str] = frozenset(
    {
        "menu",
        "menus",
        "food",
        "dishes",
        "order",
        "nutrition",
        "nutritional",
        "allergen",
        "allergens",
        "calorie",
        "calories",
    }
)


def _matches_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in MENU_KEYWORDS)


def find_menu_page_links(
    html: str, *, base_url: str, domain_verifier: DomainVerifier, limit: int = 10
) -> list[str]:
    """Returns absolute, verified-domain-only URLs for links on `html`
    whose href path or link text suggests a menu/nutrition page.

    Order is preserved (first-seen), duplicates are dropped, and results
    are capped at `limit` so a link-heavy page can't blow up the number
    of pages the caller goes on to fetch. Links outside the verified
    domain are silently dropped rather than raising — this function only
    narrows candidates, `DomainVerifier`/`DomainLock` still enforce the
    actual fetch boundary when a candidate is later crawled.
    """
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    matches: list[str] = []

    for tag in soup.find_all("a", href=True):
        href = tag.get("href")
        if not isinstance(href, str) or not href.strip():
            continue

        link_text = tag.get_text(strip=True) or ""
        if not (_matches_keyword(href) or _matches_keyword(link_text)):
            continue

        absolute_url = urljoin(base_url, href.strip())
        if not domain_verifier.is_allowed(absolute_url):
            continue
        if absolute_url in seen:
            continue

        seen.add(absolute_url)
        matches.append(absolute_url)
        if len(matches) >= limit:
            break

    return matches
