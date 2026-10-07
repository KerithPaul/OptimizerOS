"""Declarative base shared by every ORM model (app/models/, from step 1.B.3)."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
