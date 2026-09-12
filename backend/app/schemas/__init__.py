"""Re-export the schemas the API and services will consume."""
from __future__ import annotations

from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.schemas.common import ErrorResponse, Paginated
from app.schemas.course import CourseCreate, CourseResponse
from app.schemas.glossary import (
    GlossaryDocument,
    GlossaryEntry,
    GlossaryResponse,
    GlossaryTermResponse,
)
from app.schemas.lecture import LectureDetail, LectureListItem, LectureStatusResponse
from app.schemas.summary import (
    SummaryDocument,
    SummaryHighlight,
    SummaryResponse,
    SummarySection,
)
from app.schemas.transcript import (
    SegmentDraft,
    TranscriptResponse,
    TranscriptSegmentResponse,
)

__all__ = [
    "CourseCreate",
    "CourseResponse",
    "ErrorResponse",
    "GlossaryDocument",
    "GlossaryEntry",
    "GlossaryResponse",
    "GlossaryTermResponse",
    "LectureDetail",
    "LectureListItem",
    "LectureStatusResponse",
    "LoginRequest",
    "Paginated",
    "RegisterRequest",
    "SegmentDraft",
    "SummaryDocument",
    "SummaryHighlight",
    "SummaryResponse",
    "SummarySection",
    "TokenResponse",
    "TranscriptResponse",
    "TranscriptSegmentResponse",
    "UserResponse",
]
