"""Symmetric encryption for saved data-source credentials.

Connection strings and Inflectiv keys are decrypted only server-side, only
at the moment of connecting — never sent to the browser.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

import config


class CryptoError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = config.DATA_SOURCE_ENCRYPTION_KEY
    if not key:
        raise CryptoError(
            "DATA_SOURCE_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode())
    except ValueError as e:
        raise CryptoError(f"DATA_SOURCE_ENCRYPTION_KEY is not a valid Fernet key: {e}") from e


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(blob: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken as e:
        raise CryptoError("Could not decrypt stored credential — wrong key or corrupted data.") from e
