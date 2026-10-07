"""Password hashing, session-cookie signing, and the seeded-user bootstrap.

Argon2 hashing [P6]; HTTP-only signed session cookie [P7]; one seeded user
from SEED_USER_EMAIL / SEED_USER_PASSWORD, created idempotently on first
boot [CONFIRMED C2].
"""

import logging

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.user import User, UserRole

logger = logging.getLogger("architectos.security")

SESSION_COOKIE_NAME = "architectos_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 days
_SESSION_SALT = "architectos-session"

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except VerificationError:
        return False


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.app_secret_key, salt=_SESSION_SALT)


def create_session_token(user_id: int, settings: Settings) -> str:
    return _serializer(settings).dumps({"user_id": user_id})


def read_session_token(token: str, settings: Settings) -> int | None:
    try:
        data = _serializer(settings).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("user_id")


def seed_user(db: Session, settings: Settings) -> None:
    """Create the single local user from env, if it doesn't already exist.

    The plaintext password is never logged and never returned.
    """
    if not settings.seed_user_email or not settings.seed_user_password:
        logger.warning("seed_user skipped: SEED_USER_EMAIL/SEED_USER_PASSWORD not set")
        return
    existing = db.scalar(select(User).where(User.email == settings.seed_user_email))
    if existing is not None:
        promote_seed_admin(db, existing)
        return
    user = User(
        email=settings.seed_user_email,
        password_hash=hash_password(settings.seed_user_password),
        role=UserRole.ADMIN,
    )
    db.add(user)
    db.commit()
    logger.info("seeded initial user")


def promote_seed_admin(db: Session, user: User) -> None:
    """Ensure the seed account can administer users (idempotent, no-op already-admin)."""
    if user.role == UserRole.ADMIN:
        return
    user.role = UserRole.ADMIN
    db.commit()
    logger.info("promoted seed user %s to admin", user.email)
