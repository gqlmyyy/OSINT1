"""Credential encryption: the properties that make storing a token acceptable."""

from __future__ import annotations

import base64
import os

import pytest

from app.core.config import Settings
from app.security import crypto


def _settings(**overrides: object) -> Settings:
    base = {
        "env": "test",
        "secret_key": "test-secret-key-that-is-long-enough-for-checks-1234",
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_round_trip() -> None:
    settings = _settings()
    sealed = crypto.encrypt("tok-abc", aad="ctx", settings=settings)
    assert crypto.decrypt(sealed, aad="ctx", settings=settings).reveal() == "tok-abc"


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    settings = _settings()
    sealed = crypto.encrypt("SUPER-SECRET-TOKEN", aad="ctx", settings=settings)
    assert "SUPER-SECRET-TOKEN" not in sealed


def test_same_plaintext_encrypts_differently_each_time() -> None:
    """A fresh nonce per call: equal tokens must not produce equal ciphertexts."""
    settings = _settings()
    first = crypto.encrypt("same", aad="ctx", settings=settings)
    second = crypto.encrypt("same", aad="ctx", settings=settings)
    assert first != second


def test_wrong_context_cannot_decrypt() -> None:
    """The anti-row-swap property: a blob sealed for user A is useless for user B."""
    settings = _settings()
    sealed = crypto.encrypt("tok", aad=crypto.account_aad("user-a", "instagram"), settings=settings)
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(
            sealed, aad=crypto.account_aad("user-b", "instagram"), settings=settings
        )


def test_tampered_ciphertext_is_rejected() -> None:
    settings = _settings()
    sealed = crypto.encrypt("tok", aad="ctx", settings=settings)
    version, nonce, body = sealed.split(".")
    flipped = body[:-4] + ("AAAA" if not body.endswith("AAAA") else "BBBB")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(f"{version}.{nonce}.{flipped}", aad="ctx", settings=settings)


def test_a_different_secret_key_cannot_decrypt() -> None:
    sealed = crypto.encrypt("tok", aad="ctx", settings=_settings())
    rotated = _settings(secret_key="a-completely-different-secret-key-0123456789")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(sealed, aad="ctx", settings=rotated)


@pytest.mark.parametrize(
    "envelope", ["", None, "garbage", "v1.only-two", "v9.aaaa.bbbb", "v1.!!!.???"]
)
def test_malformed_envelopes_raise_rather_than_crash(envelope: str | None) -> None:
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(envelope, aad="ctx", settings=_settings())


def test_encrypt_refuses_an_empty_context() -> None:
    """Without an aad there is no row binding, so this is a programming error."""
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt("tok", aad="", settings=_settings())


def test_explicit_key_is_used_when_configured() -> None:
    raw = os.urandom(32)
    key = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    settings = _settings(token_encryption_key=key)
    assert crypto.derive_key(settings) == raw


def test_explicit_key_of_the_wrong_length_is_rejected() -> None:
    short = base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")
    with pytest.raises(crypto.CryptoError):
        crypto.derive_key(_settings(token_encryption_key=short))


def test_derived_key_is_not_the_signing_secret() -> None:
    """Domain separation: the encryption key must not be the raw JWT secret."""
    settings = _settings()
    assert crypto.derive_key(settings) != settings.secret_key.encode()
    assert len(crypto.derive_key(settings)) == crypto.KEY_BYTES


def test_secret_never_renders_its_value() -> None:
    """The last line of defence against a token reaching a log or a traceback."""
    secret = crypto.Secret("tok-do-not-print")
    assert "tok-do-not-print" not in repr(secret)
    assert "tok-do-not-print" not in str(secret)
    assert "tok-do-not-print" not in f"{secret}"
    assert "tok-do-not-print" not in "{}".format(secret)  # noqa: UP032
    assert secret.reveal() == "tok-do-not-print"


def test_secret_comparison_is_constant_time_and_typed() -> None:
    assert crypto.Secret("a") == crypto.Secret("a")
    assert crypto.Secret("a") != crypto.Secret("b")
    assert (crypto.Secret("a") == "a") is False
