"""Original-media streaming endpoint for the in-page `<audio>` player."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.models.lecture import Lecture
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lectures/{lecture_id}", tags=["media"])


_MEDIA_TYPES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
}


def _get_owned_lecture(db: DBSession, lecture_id: int, user_id: int) -> Lecture:
    lecture = (
        db.query(Lecture)
        .filter(Lecture.id == lecture_id, Lecture.user_id == user_id)
        .first()
    )
    if lecture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lecture not found")
    return lecture


@router.get("/audio")
def stream_audio(
    lecture_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> FileResponse:
    """Stream the original upload. Starlette's `FileResponse` handles Range requests."""
    lecture = _get_owned_lecture(db, lecture_id, user.id)

    settings = get_settings()
    media_dir = settings.media_root / str(lecture.user_id) / str(lecture.id)
    if not media_dir.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="audio not found")

    # Scan the folder rather than trusting the extension of `original_filename`; the file on
    # disk is always named `original.<ext>` where the extension comes from magic bytes.
    candidates = sorted(media_dir.glob("original.*"))
    if not candidates:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="audio not found")

    audio_path = candidates[0]
    media_type = _MEDIA_TYPES.get(audio_path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path=str(audio_path),
        media_type=media_type,
        filename=lecture.original_filename,
    )
