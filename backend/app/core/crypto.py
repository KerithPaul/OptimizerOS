"""Symmetric encryption for platform credentials, keyed by CREDENTIAL_ENCRYPTION_KEY [P8].

GitHub PATs (Phase 9) and other platform credentials use this helper so
they never invent a second encryption scheme.

Decryption is forbidden inside any LLM prompt path: ciphertext may be stored
and passed around freely, but a decrypted secret must never be interpolated
into a prompt, tool call payload, or anything an LLM sees.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings, get_settings


def _fernet(settings: Settings | None = None) -> Fernet:
    settings = settings or get_settings()
    return Fernet(settings.credential_encryption_key.encode())


def encrypt(plaintext: str, settings: Settings | None = None) -> bytes:
    return _fernet(settings).encrypt(plaintext.encode())


def decrypt(ciphertext: bytes, settings: Settings | None = None) -> str:
    try:
        return _fernet(settings).decrypt(ciphertext).decode()
    except InvalidToken as exc:
        raise ValueError("invalid CREDENTIAL_ENCRYPTION_KEY or corrupted ciphertext") from exc
