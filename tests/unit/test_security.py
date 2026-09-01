"""Unit tests for password hashing and JWT helpers — no DB, no app, just
apps.api.app.core.security in isolation."""

import time

import jwt
import pytest

from apps.api.app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from core.config.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_secret_key="test-secret-key-32-bytes-minimum!!",
        jwt_access_token_expire_minutes=60,
        jwt_refresh_token_expire_minutes=1440,
    )


class TestPasswordHashing:
    def test_hash_is_not_the_plaintext(self) -> None:
        hashed = hash_password("correct horse battery staple")
        assert hashed != "correct horse battery staple"

    def test_verify_succeeds_for_correct_password(self) -> None:
        hashed = hash_password("hunter2")
        assert verify_password("hunter2", hashed) is True

    def test_verify_fails_for_incorrect_password(self) -> None:
        hashed = hash_password("hunter2")
        assert verify_password("wrong-password", hashed) is False

    def test_same_password_hashes_differently_each_time(self) -> None:
        # bcrypt salts automatically — two hashes of the same password must
        # never be equal, or a rainbow-table attack becomes viable.
        assert hash_password("hunter2") != hash_password("hunter2")

    def test_hash_uses_bcrypt(self) -> None:
        assert hash_password("hunter2").startswith("$2b$")


class TestAccessToken:
    def test_round_trips_subject_and_role(self, settings: Settings) -> None:
        token = create_access_token(subject="user-123", role="VIEWER", settings=settings)
        payload = decode_token(token, settings)
        assert payload["sub"] == "user-123"
        assert payload["role"] == "VIEWER"
        assert payload["type"] == "access"

    def test_rejects_tampered_signature(self, settings: Settings) -> None:
        token = create_access_token(subject="user-123", role="VIEWER", settings=settings)
        tampered = token[:-4] + "abcd"
        with pytest.raises(jwt.PyJWTError):
            decode_token(tampered, settings)

    def test_rejects_token_signed_with_different_secret(self, settings: Settings) -> None:
        token = create_access_token(subject="user-123", role="VIEWER", settings=settings)
        other_settings = settings.model_copy(update={"api_secret_key": "a-completely-different-key"})
        with pytest.raises(jwt.PyJWTError):
            decode_token(token, other_settings)

    def test_expired_token_is_rejected(self, settings: Settings) -> None:
        short_lived = settings.model_copy(update={"jwt_access_token_expire_minutes": 0})
        token = create_access_token(subject="user-123", role="VIEWER", settings=short_lived)
        time.sleep(1.5)
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(token, short_lived)


class TestRefreshToken:
    def test_returns_raw_token_hash_and_expiry(self, settings: Settings) -> None:
        raw, token_hash, expires_at = create_refresh_token(subject="user-123", settings=settings)
        assert raw != token_hash
        assert token_hash == hash_token(raw)
        assert expires_at.tzinfo is not None

    def test_two_refresh_tokens_for_same_subject_are_unique(self, settings: Settings) -> None:
        raw1, _, _ = create_refresh_token(subject="user-123", settings=settings)
        raw2, _, _ = create_refresh_token(subject="user-123", settings=settings)
        assert raw1 != raw2

    def test_hash_token_is_deterministic(self) -> None:
        assert hash_token("same-input") == hash_token("same-input")

    def test_decoded_refresh_token_has_type_refresh(self, settings: Settings) -> None:
        raw, _, _ = create_refresh_token(subject="user-123", settings=settings)
        payload = decode_token(raw, settings)
        assert payload["type"] == "refresh"
        assert "jti" in payload
