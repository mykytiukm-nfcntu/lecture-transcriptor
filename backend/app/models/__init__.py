"""Import + re-export every ORM model so `Base.metadata` sees them at `create_all()` time."""
from __future__ import annotations

from app.models.course import Course
from app.models.glossary import GlossaryTerm
from app.models.lecture import Lecture, LectureStatus
from app.models.session import Session
from app.models.summary import Summary
from app.models.transcript import Transcript, TranscriptSegment
from app.models.user import User

__all__ = [
    "Course",
    "GlossaryTerm",
    "Lecture",
    "LectureStatus",
    "Session",
    "Summary",
    "Transcript",
    "TranscriptSegment",
    "User",
]
