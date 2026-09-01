import pytest

from infrastructure.source_authority.domain_validator import (
    DomainLockConfig,
    DomainRejectedError,
    validate_official_domain,
)


class TestValidateOfficialDomain:
    def test_accepts_plausible_restaurant_domain(self) -> None:
        normalized_url, config = validate_official_domain("https://joes-pizza.com/")
        assert normalized_url == "https://joes-pizza.com/"
        assert isinstance(config, DomainLockConfig)
        assert config.verified_domain == "joes-pizza.com"
        assert config.allowed_url == normalized_url

    def test_strips_www_when_building_domain_lock_config(self) -> None:
        _, config = validate_official_domain("https://www.joes-pizza.com/")
        assert config.verified_domain == "joes-pizza.com"

    def test_rejects_known_aggregator(self) -> None:
        with pytest.raises(DomainRejectedError, match="aggregator"):
            validate_official_domain("https://www.yelp.com/biz/joes-pizza")

    def test_rejects_aggregator_subdomain(self) -> None:
        with pytest.raises(DomainRejectedError, match="aggregator"):
            validate_official_domain("https://order.doordash.com/store/joes-pizza")

    def test_rejects_social_platform(self) -> None:
        with pytest.raises(DomainRejectedError):
            validate_official_domain("https://www.facebook.com/joespizza")

    def test_rejects_malformed_url(self) -> None:
        with pytest.raises(DomainRejectedError):
            validate_official_domain("not a url at all")

    def test_rejects_bare_hostname_with_no_dot(self) -> None:
        with pytest.raises(DomainRejectedError):
            validate_official_domain("https://localhost/")

    def test_rejects_unsupported_scheme(self) -> None:
        with pytest.raises(DomainRejectedError):
            validate_official_domain("ftp://joes-pizza.com")

    def test_accepts_url_with_extra_path_and_normalizes_it(self) -> None:
        normalized_url, config = validate_official_domain("JOES-PIZZA.com/menu/")
        assert normalized_url == "https://joes-pizza.com/menu"
        assert config.verified_domain == "joes-pizza.com"
