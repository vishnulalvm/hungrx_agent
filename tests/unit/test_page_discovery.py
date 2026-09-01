"""Unit tests for deterministic menu/nutrition page link discovery — pure
HTML parsing, no network calls, no AI."""

from infrastructure.crawler.domain_lock import DomainVerifier
from infrastructure.crawler.page_discovery import find_menu_page_links


def _verifier() -> DomainVerifier:
    return DomainVerifier("joes-pizza.com")


class TestFindMenuPageLinks:
    def test_matches_link_with_menu_in_href(self) -> None:
        html = '<a href="/menu">Explore</a>'
        links = find_menu_page_links(html, base_url="https://joes-pizza.com/", domain_verifier=_verifier())
        assert links == ["https://joes-pizza.com/menu"]

    def test_matches_link_with_menu_in_text_but_generic_href(self) -> None:
        html = '<a href="/page-2">Our Menu</a>'
        links = find_menu_page_links(html, base_url="https://joes-pizza.com/", domain_verifier=_verifier())
        assert links == ["https://joes-pizza.com/page-2"]

    def test_matches_nutrition_keyword(self) -> None:
        html = '<a href="/nutrition-facts.pdf">Nutrition Facts</a>'
        links = find_menu_page_links(html, base_url="https://joes-pizza.com/", domain_verifier=_verifier())
        assert links == ["https://joes-pizza.com/nutrition-facts.pdf"]

    def test_ignores_unrelated_links(self) -> None:
        html = """
        <a href="/about">About Us</a>
        <a href="/contact">Contact</a>
        <a href="/careers">Careers</a>
        """
        links = find_menu_page_links(html, base_url="https://joes-pizza.com/", domain_verifier=_verifier())
        assert links == []

    def test_resolves_relative_urls_against_base(self) -> None:
        html = '<a href="menu/lunch">Lunch Menu</a>'
        links = find_menu_page_links(
            html, base_url="https://joes-pizza.com/food/", domain_verifier=_verifier()
        )
        assert links == ["https://joes-pizza.com/food/menu/lunch"]

    def test_drops_links_outside_verified_domain(self) -> None:
        html = '<a href="https://order.doordash.com/store/joes-pizza">Order our menu</a>'
        links = find_menu_page_links(html, base_url="https://joes-pizza.com/", domain_verifier=_verifier())
        assert links == []

    def test_deduplicates_repeated_links(self) -> None:
        html = """
        <a href="/menu">Menu</a>
        <a href="/menu">View Menu</a>
        """
        links = find_menu_page_links(html, base_url="https://joes-pizza.com/", domain_verifier=_verifier())
        assert links == ["https://joes-pizza.com/menu"]

    def test_respects_limit(self) -> None:
        html = "".join(f'<a href="/menu-{i}">Menu {i}</a>' for i in range(20))
        links = find_menu_page_links(
            html, base_url="https://joes-pizza.com/", domain_verifier=_verifier(), limit=3
        )
        assert len(links) == 3

    def test_ignores_links_with_no_href(self) -> None:
        html = '<a name="anchor">Menu</a>'
        links = find_menu_page_links(html, base_url="https://joes-pizza.com/", domain_verifier=_verifier())
        assert links == []

    def test_ignores_empty_href(self) -> None:
        html = '<a href="   ">Menu</a>'
        links = find_menu_page_links(html, base_url="https://joes-pizza.com/", domain_verifier=_verifier())
        assert links == []

    def test_matches_case_insensitively(self) -> None:
        html = '<a href="/MENU">OUR MENU</a>'
        links = find_menu_page_links(html, base_url="https://joes-pizza.com/", domain_verifier=_verifier())
        assert links == ["https://joes-pizza.com/MENU"]

    def test_no_links_returns_empty_list(self) -> None:
        links = find_menu_page_links(
            "<html><body>No links here</body></html>",
            base_url="https://joes-pizza.com/",
            domain_verifier=_verifier(),
        )
        assert links == []
