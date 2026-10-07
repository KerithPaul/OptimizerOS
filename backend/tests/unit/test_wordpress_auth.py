"""WordPress Application Password storage and redaction (step 10.1)."""

from cryptography.fernet import Fernet

from app.connectors.wordpress.auth import encode_credentials, redact_secret
from app.core.config import Settings
from app.core.crypto import encrypt


def test_application_password_spaces_are_stripped() -> None:
    blob = encode_credentials("admin", "aaaa bbbb cccc dddd")
    assert " " not in blob
    assert "aaaabbbbccccdddd" in blob


def test_ciphertext_is_not_the_password() -> None:
    settings = Settings(
        APP_SECRET_KEY="unit-test-secret",
        CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    secret = "aaaabbbbccccdddd"
    ciphertext = encrypt(
        encode_credentials("admin", secret),
        settings,
    )
    assert secret.encode() not in ciphertext


def test_redact_secret_strips_basic_auth() -> None:
    secret = "aaaabbbbccccdddd"
    text = f"https://admin:{secret}@golden-c.example/wp-json/"
    redacted = redact_secret(text, secret)
    assert secret not in redacted
    assert "***" in redacted
