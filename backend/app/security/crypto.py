"""Authenticated encryption for credentials held at rest.

The platform had no secret-storage primitive before OAuth tokens needed one, so this is
it. The design constraints, in order of importance:

* **Authenticated.** AES-256-GCM, so a tampered ciphertext fails loudly instead of
  decrypting to attacker-chosen bytes.
* **Context-bound.** Every ciphertext is sealed with additional authenticated data
  naming the row it belongs to (owner + provider). A blob lifted from one user's row and
  pasted into another's does not decrypt — the database alone cannot be used to move a
  credential between accounts.
* **Versioned.** The envelope carries a version tag, so the key or algorithm can be
  rotated later without guessing how existing rows were written.
* **Never printed.** :class:`Secret` wraps plaintext so an accidental ``repr``/``str``,
  log call, or traceback renders a placeholder rather than the credential.

The key comes from ``TOKEN_ENCRYPTION_KEY`` when set (32 raw bytes, base64), and is
otherwise derived from ``SECRET_KEY`` with HKDF-SHA256 under a fixed, purpose-specific
info string. Deriving means a single-secret deployment still gets a real key rather than
a weak home-made one, and the purpose string keeps it separate from the JWT signing use
of the same secret. Rotating ``SECRET_KEY`` invalidates stored ciphertexts by design:
they are re-obtainable by reconnecting, and silently keeping them readable under a
retired secret would defeat the point of rotating it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings

#: Envelope prefix. Bump when the algorithm or key derivation changes.
VERSION = "v1"
KEY_BYTES = 32
NONCE_BYTES = 12
#: Domain separation: this key must never coincide with the JWT signing key, even though
#: both can originate from SECRET_KEY.
HKDF_INFO = b"graphintel.token-encryption.v1"


class CryptoError(Exception):
    """Base class for encryption failures."""


class DecryptionError(CryptoError):
    """Ciphertext was absent, malformed, tampered with, or sealed for another context."""


class Secret:
    """A string that refuses to render itself.

    Guards against the most common way credentials leak: something in the call chain
    formatting an object into a log line, an error message, or a traceback frame.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Explicit, greppable access. Every call site is an auditable decision."""
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value, other._value)

    def __hash__(self) -> int:  # pragma: no cover - defined for dict/set safety only
        return hash(("Secret", self._value))


def _hkdf_sha256(secret: bytes, *, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF. Implemented on hashlib to avoid a second crypto backend import."""
    prk = hmac.new(salt, secret, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def derive_key(settings: Settings | None = None) -> bytes:
    """Return the 32-byte data-encryption key for this deployment."""
    settings = settings or get_settings()
    configured = settings.token_encryption_key
    if configured:
        try:
            raw = base64.urlsafe_b64decode(_pad_b64(configured.strip()))
        except (ValueError, TypeError) as exc:
            raise CryptoError(
                "TOKEN_ENCRYPTION_KEY must be url-safe base64 of exactly 32 bytes"
            ) from exc
        if len(raw) != KEY_BYTES:
            raise CryptoError(
                f"TOKEN_ENCRYPTION_KEY must decode to {KEY_BYTES} bytes, got {len(raw)}"
            )
        return raw
    return _hkdf_sha256(
        settings.secret_key.encode(),
        salt=b"graphintel.token-encryption.salt",
        info=HKDF_INFO,
        length=KEY_BYTES,
    )


def _pad_b64(value: str) -> str:
    return value + "=" * (-len(value) % 4)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def encrypt(plaintext: str, *, aad: str, settings: Settings | None = None) -> str:
    """Seal ``plaintext`` for the context named by ``aad``.

    ``aad`` is authenticated but not encrypted: it must be reproducible at decrypt time
    from the row being read (owner id + provider), which is what binds a ciphertext to
    exactly one row.
    """
    if not aad:
        raise CryptoError("refusing to encrypt without a context (aad)")
    key = derive_key(settings)
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode(), aad.encode())
    return f"{VERSION}.{_b64(nonce)}.{_b64(sealed)}"


def decrypt(envelope: str | None, *, aad: str, settings: Settings | None = None) -> Secret:
    """Open an envelope produced by :func:`encrypt`, or raise :class:`DecryptionError`."""
    if not envelope:
        raise DecryptionError("no stored ciphertext")
    parts = envelope.split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        raise DecryptionError("unrecognised ciphertext envelope")
    try:
        nonce = base64.urlsafe_b64decode(_pad_b64(parts[1]))
        sealed = base64.urlsafe_b64decode(_pad_b64(parts[2]))
    except (ValueError, TypeError) as exc:
        raise DecryptionError("ciphertext is not valid base64") from exc
    if len(nonce) != NONCE_BYTES:
        raise DecryptionError("ciphertext nonce has the wrong length")
    try:
        opened = AESGCM(derive_key(settings)).decrypt(nonce, sealed, aad.encode())
    except InvalidTag as exc:
        # Tampering, a rotated key, or a blob sealed for a different row. All three are
        # the same answer to the caller: this credential is not usable.
        raise DecryptionError("ciphertext failed authentication") from exc
    return Secret(opened.decode())


def account_aad(owner_id: Any, provider: str) -> str:
    """The binding context for a linked-account credential."""
    return f"linked_account|{owner_id}|{provider}"


__all__ = [
    "CryptoError",
    "DecryptionError",
    "Secret",
    "account_aad",
    "decrypt",
    "derive_key",
    "encrypt",
]
