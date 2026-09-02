"""Unit tests for apps/api/app/middleware/request_context.py's
_sanitize_request_id — a client-supplied X-Request-ID is echoed into
structured logs and the response, so it's capped/charset-restricted
rather than accepted verbatim (security review fix)."""

from apps.api.app.middleware.request_context import (
    _MAX_REQUEST_ID_LENGTH,
    _sanitize_request_id,
)


class TestSanitizeRequestId:
    def test_none_generates_a_fresh_id(self) -> None:
        assert _sanitize_request_id(None) != ""

    def test_empty_string_generates_a_fresh_id(self) -> None:
        result = _sanitize_request_id("")
        assert result != ""

    def test_well_formed_value_is_preserved(self) -> None:
        assert _sanitize_request_id("abc-123_XYZ.456") == "abc-123_XYZ.456"

    def test_uuid_is_preserved(self) -> None:
        value = "550e8400-e29b-41d4-a716-446655440000"
        assert _sanitize_request_id(value) == value

    def test_oversized_value_is_replaced(self) -> None:
        oversized = "a" * (_MAX_REQUEST_ID_LENGTH + 1)
        result = _sanitize_request_id(oversized)
        assert result != oversized

    def test_value_at_the_length_cap_is_preserved(self) -> None:
        exact = "a" * _MAX_REQUEST_ID_LENGTH
        assert _sanitize_request_id(exact) == exact

    def test_disallowed_characters_are_replaced(self) -> None:
        for bad_value in ("has spaces", "has\nnewline", "has\ttab", 'has"quote', "has<tag>"):
            result = _sanitize_request_id(bad_value)
            assert result != bad_value
