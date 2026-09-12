"""Summary artifact row (JSON-serialised `SummaryDocument` in `content_json`)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UtcDateTime

if TYPE_CHECKING:
    from app.models.lecture import Lecture


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lecture_id: Mapped[int] = mapped_column(
        ForeignKey("lectures.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    # Stringified JSON; the API decodes via `SummaryDocument.model_validate_json`.
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_language: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    lecture: Mapped["Lecture"] = relationship(back_populates="summary")
