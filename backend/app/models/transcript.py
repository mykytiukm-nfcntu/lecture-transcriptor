"""Transcript aggregate + its ordered segments (one Transcript per Lecture)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UtcDateTime

if TYPE_CHECKING:
    from app.models.lecture import Lecture


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lecture_id: Mapped[int] = mapped_column(
        ForeignKey("lectures.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    lecture: Mapped["Lecture"] = relationship(back_populates="transcript")
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TranscriptSegment.index",
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint("transcript_id", "index", name="uq_transcript_segment_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transcript_id: Mapped[int] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    transcript: Mapped["Transcript"] = relationship(back_populates="segments")
