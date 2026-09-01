import pytest

from infrastructure.source_authority.url_normalizer import InvalidUrlError, normalize_url


class TestNormalizeUrl:
    def test_adds_https_scheme_when_missing(self) -> None:
        assert normalize_url("example.com") == "https://example.com/"

    def test_lowercases_scheme_and_host(self) -> None:
        assert normalize_url("HTTPS://Example.COM") == "https://example.com/"

    def test_collapses_empty_path_to_root(self) -> None:
        assert normalize_url("https://example.com") == "https://example.com/"

    def test_strips_trailing_slash_on_non_root_path(self) -> None:
        assert normalize_url("https://example.com/menu/") == "https://example.com/menu"

    def test_keeps_root_path_as_slash(self) -> None:
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_strips_default_https_port(self) -> None:
        assert normalize_url("https://example.com:443/menu") == "https://example.com/menu"

    def test_strips_default_http_port(self) -> None:
        assert normalize_url("http://example.com:80/menu") == "http://example.com/menu"

    def test_keeps_non_default_port(self) -> None:
        assert normalize_url("https://example.com:8443/menu") == "https://example.com:8443/menu"

    def test_drops_fragment(self) -> None:
        assert normalize_url("https://example.com/menu#section") == "https://example.com/menu"

    def test_strips_utm_tracking_params(self) -> None:
        result = normalize_url("https://example.com/?utm_source=google&utm_medium=cpc")
        assert result == "https://example.com/"

    def test_strips_click_id_tracking_params(self) -> None:
        result = normalize_url("https://example.com/?gclid=abc123&fbclid=xyz")
        assert result == "https://example.com/"

    def test_keeps_non_tracking_query_params(self) -> None:
        result = normalize_url("https://example.com/menu?location=downtown")
        assert result == "https://example.com/menu?location=downtown"

    def test_mixed_tracking_and_real_params_keeps_only_real(self) -> None:
        result = normalize_url("https://example.com/?utm_source=x&location=downtown")
        assert result == "https://example.com/?location=downtown"

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(InvalidUrlError):
            normalize_url("")

    def test_rejects_whitespace_only_string(self) -> None:
        with pytest.raises(InvalidUrlError):
            normalize_url("   ")

    def test_rejects_unsupported_scheme(self) -> None:
        with pytest.raises(InvalidUrlError):
            normalize_url("ftp://example.com")

    def test_rejects_url_with_no_hostname(self) -> None:
        with pytest.raises(InvalidUrlError):
            normalize_url("https:///path-only")

    def test_is_idempotent(self) -> None:
        once = normalize_url("Example.com/Menu/?utm_source=x")
        twice = normalize_url(once)
        assert once == twice
