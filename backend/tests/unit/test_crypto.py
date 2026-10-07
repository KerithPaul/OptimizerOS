"""Round-trip and wrong-key behavior for the credential encryption helper (step 1.B.6 verify)."""

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.core.crypto import decrypt, encrypt


def _settings(key: str) -> Settings:
    return Settings(APP_SECRET_KEY="unit-test-secret", CREDENTIAL_ENCRYPTION_KEY=key)


def test_round_trip() -> None:
    settings = _settings(Fernet.generate_key().decode())
    ciphertext = encrypt("super-secret-token", settings)

    assert isinstance(ciphertext, bytes)
    assert decrypt(ciphertext, settings) == "super-secret-token"


def test_wrong_key_raises() -> None:
    right = _settings(Fernet.generate_key().decode())
    wrong = _settings(Fernet.generate_key().decode())

    ciphertext = encrypt("super-secret-token", right)

    with pytest.raises(ValueError):
        decrypt(ciphertext, wrong)
