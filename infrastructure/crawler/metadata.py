"""Lightweight page metadata extraction from HTML — title, description,
canonical URL, and Open Graph tags. Deliberately not part of AI
extraction: this is structural metadata parsed straight out of <head>,
not anything inferred/generated."""

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict


class PageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    canonical_url: str | None = None
    og_title: str | None = None
    og_description: str | None = None
    og_image: str | None = None


def extract_page_metadata(html: str) -> PageMetadata:
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    def meta_content(**attrs: str) -> str | None:
        tag = soup.find("meta", attrs=attrs)
        if tag is None:
            return None
        content = tag.get("content")
        return content.strip() if isinstance(content, str) else None

    canonical_tag = soup.find("link", rel="canonical")
    canonical_url = None
    if canonical_tag is not None:
        href = canonical_tag.get("href")
        canonical_url = href.strip() if isinstance(href, str) else None

    return PageMetadata(
        title=title,
        description=meta_content(name="description"),
        canonical_url=canonical_url,
        og_title=meta_content(property="og:title"),
        og_description=meta_content(property="og:description"),
        og_image=meta_content(property="og:image"),
    )
