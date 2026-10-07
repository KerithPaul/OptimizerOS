"""Encrypted WordPress Application Password on `platform_connections`.

The username and application password are stored as one Fernet blob and
decrypted only inside the WordPress connector. They are never returned
by an API, never logged, and never placed in an LLM prompt.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.capabilities import (
    WORDPRESS_AUTH_TYPE,
    WORDPRESS_PLATFORM,
    dump_report,
    wordpress_capabilities,
)
from app.core.crypto import decrypt, encrypt
from app.models.website import PlatformConnection, Website

_SECRET_IN_URL = re.compile(r"(://[^:/]+:)[^@]+@", re.IGNORECASE)


class WordPressAuthError(Exception):
    """No usable Application Password, or the ciphertext cannot be decrypted."""


def wordpress_connection(db: Session, project_id: int) -> PlatformConnection | None:
    return db.scalar(
        select(PlatformConnection).where(
            PlatformConnection.project_id == project_id,
            PlatformConnection.platform == WORDPRESS_PLATFORM,
        )
    )


def encode_credentials(username: str, application_password: str) -> str:
    cleaned_user = username.strip()
    cleaned_password = application_password.replace(" ", "").strip()
    if not cleaned_user:
        raise WordPressAuthError("WordPress username must not be empty")
    if not cleaned_password:
        raise WordPressAuthError("WordPress application password must not be empty")
    return json.dumps(
        {"username": cleaned_user, "application_password": cleaned_password},
        separators=(",", ":"),
    )


def decode_credentials(blob: str) -> tuple[str, str]:
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise WordPressAuthError("WordPress credentials are not valid JSON") from exc
    username = payload.get("username")
    password = payload.get("application_password")
    if not isinstance(username, str) or not username.strip():
        raise WordPressAuthError("WordPress username is missing")
    if not isinstance(password, str) or not password.strip():
        raise WordPressAuthError("WordPress application password is missing")
    return username, password.replace(" ", "")


def store_application_password(
    db: Session,
    *,
    project_id: int,
    website_id: int,
    username: str,
    application_password: str,
    capabilities: dict | None = None,
) -> PlatformConnection:
    blob = encode_credentials(username, application_password)
    report = capabilities or dump_report(wordpress_capabilities())
    connection = wordpress_connection(db, project_id)
    if connection is None:
        connection = PlatformConnection(
            website_id=website_id,
            project_id=project_id,
            platform=WORDPRESS_PLATFORM,
            auth_type=WORDPRESS_AUTH_TYPE,
            capabilities_json=report,
            credentials_encrypted=encrypt(blob),
        )
        db.add(connection)
    else:
        connection.website_id = website_id
        connection.auth_type = WORDPRESS_AUTH_TYPE
        connection.capabilities_json = report
        connection.credentials_encrypted = encrypt(blob)
    db.commit()
    db.refresh(connection)
    return connection


def clear_application_password(db: Session, project_id: int) -> None:
    connection = wordpress_connection(db, project_id)
    if connection is None:
        return
    db.delete(connection)
    db.commit()


def decrypt_application_password(connection: PlatformConnection) -> tuple[str, str]:
    if connection.platform != WORDPRESS_PLATFORM:
        raise WordPressAuthError("platform_connection is not WordPress")
    if not connection.credentials_encrypted:
        raise WordPressAuthError("WordPress application password is not stored")
    return decode_credentials(decrypt(connection.credentials_encrypted))


def require_application_password(
    db: Session, project_id: int
) -> tuple[PlatformConnection, str, str]:
    connection = wordpress_connection(db, project_id)
    if connection is None:
        raise WordPressAuthError(
            "WordPress application password is not connected for this project"
        )
    username, password = decrypt_application_password(connection)
    return connection, username, password


def redact_secret(text: str, secret: str | None) -> str:
    if not text:
        return text
    redacted = _SECRET_IN_URL.sub(r"\1***@", text)
    if secret:
        redacted = redacted.replace(secret, "***")
    return redacted


def website_for_wordpress(db: Session, project_id: int) -> Website | None:
    return db.scalar(select(Website).where(Website.project_id == project_id))
