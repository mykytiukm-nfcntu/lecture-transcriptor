"""Lecture routes: nested list/upload under a course, plus flat detail/delete."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session as DBSession

from app.api.deps import get_current_user
from app.core import job_lock
from app.core.config import get_settings
from app.core.db import get_db
from app.core.job_lock import LockedError
from app.models.course import Course
from app.models.lecture import Lecture, LectureStatus
from app.models.user import User
from app.schemas.lecture import LectureDetail, LectureListItem
from app.services import media as media_svc
from app.workers import worker

logger = logging.getLogger(__name__)


class LectureUploadAccepted(LectureListItem):
    """202 response for `POST /api/courses/{course_id}/lectures`.

    Extends `LectureListItem` with a preliminary ETA shown to the user before the pipeline
    actually starts. `None` when duration probing did not run (upload rejected earlier).
    """

    preliminary_eta_seconds: float | None = None


# Endpoints nested under a course.
nested_router = APIRouter(
    prefix="/api/courses/{course_id}/lectures",
    tags=["lectures"],
)

# Endpoints keyed only by lecture id.
detail_router = APIRouter(prefix="/api/lectures", tags=["lectures"])


_ACTIVE_STATUSES: frozenset[LectureStatus] = frozenset(
    {LectureStatus.normalizing, LectureStatus.transcribing, LectureStatus.generating}
)


def _get_owned_course(db: DBSession, course_id: int, user_id: int) -> Course:
    course = (
        db.query(Course)
        .filter(Course.id == course_id, Course.user_id == user_id)
        .first()
    )
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="course not found")
    return course


def _get_owned_lecture(db: DBSession, lecture_id: int, user_id: int) -> Lecture:
    lecture = (
        db.query(Lecture)
        .filter(Lecture.id == lecture_id, Lecture.user_id == user_id)
        .first()
    )
    if lecture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lecture not found")
    return lecture


def _mark_failed_now(db: DBSession, lecture: Lecture, message: str) -> None:
    lecture.status = LectureStatus.failed
    lecture.error_message = message[:2000]
    db.commit()


@nested_router.get("", response_model=list[LectureListItem])
def list_lectures(
    course_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> list[Lecture]:
    """List lectures for a course owned by the current user."""
    _get_owned_course(db, course_id, user.id)
    return (
        db.query(Lecture)
        .filter(Lecture.course_id == course_id, Lecture.user_id == user.id)
        .order_by(Lecture.created_at.desc(), Lecture.id.desc())
        .all()
    )


@nested_router.post(
    "",
    response_model=LectureUploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_lecture(
    course_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
    file: Annotated[UploadFile, File(description="MP3 or WAV recording of the lecture.")],
    title: str | None = None,
    language: str | None = None,
) -> LectureUploadAccepted:
    """Accept a lecture upload, persist it, and kick off background processing."""
    _get_owned_course(db, course_id, user.id)

    # Reject before touching the DB when a job is already active.
    if job_lock.is_locked() or not job_lock.job_queue.empty():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="another transcription is currently running",
        )

    try:
        media_svc.validate_mime(file.content_type)
    except media_svc.UnsupportedMediaTypeError as exc:
        logger.info("rejected upload: content-type %r", file.content_type)
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="only mp3 or wav files are accepted",
        ) from exc

    original_filename = file.filename or "upload"
    derived_title = title or Path(original_filename).stem or "Untitled lecture"

    lecture = Lecture(
        course_id=course_id,
        user_id=user.id,
        title=derived_title,
        original_filename=original_filename,
        status=LectureStatus.queued,
    )
    db.add(lecture)
    db.flush()  # obtain lecture.id without committing yet

    try:
        saved_path = media_svc.save_upload(user.id, lecture.id, original_filename, file.file)
    except media_svc.UnsupportedMediaTypeError as exc:
        db.delete(lecture)
        db.commit()
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except media_svc.MediaError as exc:
        db.delete(lecture)
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        lecture.duration_seconds = media_svc.probe_duration(saved_path)
    except media_svc.MediaError as exc:
        db.delete(lecture)
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.commit()
    db.refresh(lecture)

    try:
        worker.enqueue(lecture.id, generation_language=language)
    except LockedError as exc:
        db.delete(lecture)
        db.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="another transcription is currently running",
        ) from exc

    eta = (
        media_svc.estimated_processing_seconds(lecture.duration_seconds)
        if lecture.duration_seconds is not None
        else None
    )
    base = LectureListItem.model_validate(lecture).model_dump()
    return LectureUploadAccepted(**base, preliminary_eta_seconds=eta)


@detail_router.get("/{lecture_id}", response_model=LectureDetail)
def get_lecture(
    lecture_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> Lecture:
    """Return one lecture; non-owner or missing → 404."""
    return _get_owned_lecture(db, lecture_id, user.id)


@detail_router.delete("/{lecture_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lecture(
    lecture_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> Response:
    """Delete a lecture and its on-disk media folder. In-progress → 409."""
    lecture = _get_owned_lecture(db, lecture_id, user.id)
    if lecture.status in _ACTIVE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="cannot delete while processing",
        )

    user_id = lecture.user_id
    lid = lecture.id
    db.delete(lecture)
    db.commit()

    # Best-effort disk cleanup; missing directory is not an error.
    settings = get_settings()
    media_dir = settings.media_root / str(user_id) / str(lid)
    shutil.rmtree(media_dir, ignore_errors=True)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
