from infrastructure.source_authority.aggregator_blocklist import is_known_aggregator


class TestIsKnownAggregator:
    def test_exact_match_is_flagged(self) -> None:
        assert is_known_aggregator("yelp.com")

    def test_subdomain_of_aggregator_is_flagged(self) -> None:
        assert is_known_aggregator("order.doordash.com")

    def test_deeply_nested_subdomain_is_flagged(self) -> None:
        assert is_known_aggregator("restaurant.menu.grubhub.com")

    def test_case_insensitive(self) -> None:
        assert is_known_aggregator("YELP.COM")

    def test_genuine_restaurant_domain_is_not_flagged(self) -> None:
        assert not is_known_aggregator("joes-pizza.com")

    def test_domain_that_merely_contains_aggregator_name_is_not_flagged(self) -> None:
        # "yelp" appearing inside a different domain must not false-positive
        assert not is_known_aggregator("notyelp.com")

    def test_social_platform_domains_are_flagged(self) -> None:
        assert is_known_aggregator("facebook.com")
        assert is_known_aggregator("instagram.com")

    def test_delivery_marketplace_domains_are_flagged(self) -> None:
        assert is_known_aggregator("ubereats.com")
        assert is_known_aggregator("grubhub.com")
