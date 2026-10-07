"""`users` — Phase 1 (login accounts) + admin profile fields.

`name`, `role`, and `status` back the admin user-management UI (step for
/admin/users). Defaults keep every pre-existing row a valid active member;
the seeded user (security.seed_user) is promoted to admin by
promote_seed_admin so single-operator installs can manage others.
"""

from datetime import datetime
import enum

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MEMBER = "member"


# How many projects a member may create. None means unlimited (admins).
ProjectLimit = int | None


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=UserRole.MEMBER.value,
        nullable=False,
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(
            UserStatus,
            name="user_status",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=UserStatus.ACTIVE.value,
        nullable=False,
    )
    # How many projects this account may create. NULL = unlimited. Enforced
    # only for role == 'member'; admins are always unlimited regardless of
    # the stored value. Backed by migration d7e2f8a4c1b9.
    project_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
