"""Admin user management (list / create / update / delete).

Every route requires an authenticated admin (user.role == 'admin'), so a
member hitting /admin/users gets 403 rather than a silent 404. Deleting or
demoting yourself is rejected so an instance can never be locked out of
its own admin panel. Passwords are Argon2-hashed (app.core.security) and
never returned.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_user
from app.core.security import hash_password
from app.db.session import get_db
from app.models.user import User, UserRole, UserStatus

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return current_user


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    role: UserRole
    status: UserStatus
    # NULL = unlimited. Only enforced for role == 'member'.
    project_limit: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.MEMBER
    project_limit: int | None = Field(default=None, ge=1, le=100)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: UserRole | None = None
    status: UserStatus | None = None
    project_limit: int | None = Field(default=None, ge=1, le=100)
    # Distinguish "not provided" from "set back to unlimited" (null).
    project_limit_set: bool = False


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> User:
    email = payload.email.strip().lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a user with this email already exists")

    user = User(
        name=payload.name.strip(),
        email=email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        # Admins are always unlimited; store the chosen limit for members.
        project_limit=None if payload.role == UserRole.ADMIN else payload.project_limit,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")

    if payload.email is not None:
        email = payload.email.strip().lower()
        clash = db.scalar(select(User).where(User.email == email, User.id != user_id))
        if clash is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "a user with this email already exists")
        user.email = email

    if payload.name is not None:
        user.name = payload.name.strip()
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    if payload.role is not None:
        if user.id == current_user.id and payload.role != UserRole.ADMIN:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "you cannot demote yourself")
        user.role = payload.role
    if payload.status is not None:
        if user.id == current_user.id and payload.status != UserStatus.ACTIVE:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "you cannot suspend yourself")
        user.status = payload.status
    if payload.project_limit_set:
        user.project_limit = None if user.role == UserRole.ADMIN else payload.project_limit

    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Response:
    if user_id == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "you cannot delete your own account")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    db.delete(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
