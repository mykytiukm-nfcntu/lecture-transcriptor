"""Course: user-scoped container for lectures."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UtcDateTime

if TYPE_CHECKING:
    from app.models.lecture import Lecture
    from app.models.user import User


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    owner: Mapped["User"] = relationship(back_populates="courses")
    lectures: Mapped[list["Lecture"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
