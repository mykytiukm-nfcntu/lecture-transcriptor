"""Opaque bearer-token session row.

The ORM class here is named `Session`, which shadows `sqlalchemy.orm.Session`.
Never write `from sqlalchemy.orm import Session` in this module or in any module
that also needs this class; import the DB session under a different alias
(e.g. `from sqlalchemy.orm import Session as DBSession`).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UtcDateTime

if TYPE_CHECKING:
    from app.models.user import User


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="sessions")
