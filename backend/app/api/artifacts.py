"""Read-only artifact endpoints: transcript, summary, glossary, status."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as DBSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.progress import get_progress
from app.models.glossary import GlossaryTerm
from app.models.lecture import Lecture, LectureStatus
from app.models.summary import Summary
from app.models.user import User
from app.schemas.glossary import GlossaryResponse, GlossaryTermResponse
from app.schemas.lecture import LectureStatusResponse
from app.schemas.summary import SummaryDocument, SummaryResponse
from app.schemas.transcript import TranscriptResponse, TranscriptSegmentResponse
from app.services import media as media_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lectures/{lecture_id}", tags=["artifacts"])


_ACTIVE_STATUSES: frozenset[LectureStatus] = frozenset(
    {LectureStatus.normalizing, LectureStatus.transcribing, LectureStatus.generating}
)


def _get_owned_lecture(db: DBSession, lecture_id: int, user_id: int) -> Lecture:
    lecture = (
        db.query(Lecture)
        .filter(Lecture.id == lecture_id, Lecture.user_id == user_id)
        .first()
    )
    if lecture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lecture not found")
    return lecture


@router.get("/transcript", response_model=TranscriptResponse)
def get_transcript(
    lecture_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
    offset: int = 0,
    limit: int | None = None,
) -> TranscriptResponse:
    """Return the transcript with optionally-paginated segments."""
    lecture = _get_owned_lecture(db, lecture_id, user.id)
    transcript = lecture.transcript
    if transcript is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="transcript not available")

    all_segments = list(transcript.segments)
    start = max(0, offset)
    end = len(all_segments) if limit is None else start + max(0, limit)
    window = all_segments[start:end]

    return TranscriptResponse(
        language=transcript.language,
        full_text=transcript.full_text,
        segments=[TranscriptSegmentResponse.model_validate(seg) for seg in window],
    )


@router.get("/summary", response_model=SummaryResponse)
def get_summary(
    lecture_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> SummaryResponse:
    """Return the summary artifact, deserialising `content_json` on the way out."""
    lecture = _get_owned_lecture(db, lecture_id, user.id)
    summary: Summary | None = lecture.summary
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="summary not available")
    document = SummaryDocument.model_validate_json(summary.content_json)
    return SummaryResponse(
        content=document,
        prompt_version=summary.prompt_version,
        generation_language=summary.generation_language,
        created_at=summary.created_at,
    )


@router.get("/glossary", response_model=GlossaryResponse)
def get_glossary(
    lecture_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> GlossaryResponse:
    """Return the glossary artifact.

    * No rows and lecture is `completed` → 200 with an empty list (edge case).
    * No rows and lecture is not yet completed → 404.
    """
    lecture = _get_owned_lecture(db, lecture_id, user.id)
    terms = (
        db.query(GlossaryTerm)
        .filter(GlossaryTerm.lecture_id == lecture.id)
        .order_by(GlossaryTerm.first_mention_seconds, GlossaryTerm.id)
        .all()
    )
    if not terms:
        if lecture.status == LectureStatus.completed:
            return GlossaryResponse(
                language=lecture.language or "",
                prompt_version="",
                entries=[],
                created_at=lecture.finished_at or lecture.created_at,
            )
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="glossary not available")

    first = terms[0]
    return GlossaryResponse(
        language=first.generation_language,
        prompt_version=first.prompt_version,
        entries=[GlossaryTermResponse.model_validate(t) for t in terms],
        created_at=first.created_at,
    )


@router.get("/status", response_model=LectureStatusResponse)
def get_status(
    lecture_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> LectureStatusResponse:
    """Return a status snapshot for the frontend's polling loop."""
    lecture = _get_owned_lecture(db, lecture_id, user.id)

    elapsed: float | None = None
    if lecture.started_at is not None:
        if lecture.status in _ACTIVE_STATUSES:
            now = datetime.now(timezone.utc)
            elapsed = max(0.0, (now - lecture.started_at).total_seconds())
        elif lecture.finished_at is not None:
            elapsed = max(0.0, (lecture.finished_at - lecture.started_at).total_seconds())

    eta = (
        media_svc.estimated_processing_seconds(lecture.duration_seconds)
        if lecture.duration_seconds is not None
        else None
    )

    progress_percent: float | None = None
    if lecture.status in _ACTIVE_STATUSES:
        snapshot = get_progress(lecture_id)
        if snapshot is not None:
            progress_percent = snapshot.percent

    return LectureStatusResponse(
        id=lecture.id,
        status=lecture.status,
        started_at=lecture.started_at,
        finished_at=lecture.finished_at,
        error_message=lecture.error_message,
        elapsed_seconds=elapsed,
        preliminary_eta_seconds=eta,
        progress_percent=progress_percent,
    )
