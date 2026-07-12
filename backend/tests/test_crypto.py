import pytest
from cryptography.fernet import Fernet

import config
import crypto


def test_encrypt_decrypt_round_trip(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    blob = crypto.encrypt("postgresql://user:pass@host:5432/db")
    assert isinstance(blob, bytes)
    assert b"pass" not in blob
    assert crypto.decrypt(blob) == "postgresql://user:pass@host:5432/db"


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE_ENCRYPTION_KEY", "")
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt("secret")


def test_wrong_key_raises_on_decrypt(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    blob = crypto.encrypt("secret")
    monkeypatch.setattr(config, "DATA_SOURCE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(blob)
