"""Unit tests for the crawler's SHA-256 hashing helper."""

import hashlib

from infrastructure.crawler.hashing import sha256_hex


class TestSha256Hex:
    def test_matches_stdlib_hashlib(self) -> None:
        content = b"<html><body>Menu</body></html>"
        assert sha256_hex(content) == hashlib.sha256(content).hexdigest()

    def test_is_deterministic(self) -> None:
        content = b"same bytes every time"
        assert sha256_hex(content) == sha256_hex(content)

    def test_different_content_produces_different_hash(self) -> None:
        assert sha256_hex(b"content A") != sha256_hex(b"content B")

    def test_empty_content_hashes_to_known_sha256_empty_digest(self) -> None:
        assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_returns_64_char_lowercase_hex_string(self) -> None:
        digest = sha256_hex(b"hungrx")
        assert len(digest) == 64
        assert digest == digest.lower()
        assert all(c in "0123456789abcdef" for c in digest)

    def test_single_byte_change_produces_completely_different_hash(self) -> None:
        a = sha256_hex(b"Cheeseburger $10.99")
        b = sha256_hex(b"Cheeseburger $10.98")
        assert a != b
