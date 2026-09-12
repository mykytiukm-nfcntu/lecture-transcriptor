"""Per-lecture glossary term row (10-30 per lecture; enforced at generation time)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UtcDateTime

if TYPE_CHECKING:
    from app.models.lecture import Lecture


class GlossaryTerm(Base):
    __tablename__ = "glossary_terms"
    __table_args__ = (UniqueConstraint("lecture_id", "term", name="uq_glossary_lecture_term"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lecture_id: Mapped[int] = mapped_column(
        ForeignKey("lectures.id", ondelete="CASCADE"), index=True, nullable=False
    )
    term: Mapped[str] = mapped_column(String(255), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    first_mention_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_language: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    lecture: Mapped["Lecture"] = relationship(back_populates="glossary_terms")
