"""Lecture row plus the pipeline status enum shared with the schema layer."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UtcDateTime

if TYPE_CHECKING:
    from app.models.course import Course
    from app.models.glossary import GlossaryTerm
    from app.models.summary import Summary
    from app.models.transcript import Transcript
    from app.models.user import User


# Keep the `(str, Enum)` mixin instead of `StrEnum`: str(member) differs between the two,
# and the JSON wire format across the app depends on the current mixin shape.
class LectureStatus(str, Enum):  # noqa: UP042
    """Pipeline lifecycle states, serialised straight to JSON via the str base."""

    queued = "queued"
    normalizing = "normalizing"
    transcribing = "transcribing"
    generating = "generating"
    completed = "completed"
    failed = "failed"


# Mirror LectureStatus's `(str, Enum)` shape so both enums serialise identically over JSON.
class StageCheckpoint(str, Enum):  # noqa: UP042
    """Last coarse pipeline stage whose artifact committed successfully for this lecture."""

    normalize = "normalize"
    transcribe = "transcribe"
    summary = "summary"
    glossary = "glossary"


class Lecture(Base):
    __tablename__ = "lectures"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Denormalised owner for cheap ownership checks that skip the courses join.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Original upload overrides, preserved so a retry re-runs with the same choices.
    generation_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ollama_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[LectureStatus] = mapped_column(
        SAEnum(LectureStatus, name="lecture_status"),
        default=LectureStatus.queued,
        index=True,
        nullable=False,
    )
    # Checkpoint watermark advanced by the pipeline after every stage commit; NULL
    # for legacy rows that failed before this feature shipped (grandfathered, no retry).
    last_completed_stage: Mapped[StageCheckpoint | None] = mapped_column(
        SAEnum(StageCheckpoint, name="stage_checkpoint"),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    course: Mapped[Course] = relationship(back_populates="lectures")
    owner: Mapped[User] = relationship()
    transcript: Mapped[Transcript | None] = relationship(
        back_populates="lecture",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    summary: Mapped[Summary | None] = relationship(
        back_populates="lecture",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    glossary_terms: Mapped[list[GlossaryTerm]] = relationship(
        back_populates="lecture",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def can_retry(self) -> bool:
        """True when a failed lecture has a checkpoint that still leaves work to redo."""
        return (
            self.status == LectureStatus.failed
            and self.last_completed_stage is not None
            and self.last_completed_stage != StageCheckpoint.glossary
        )
