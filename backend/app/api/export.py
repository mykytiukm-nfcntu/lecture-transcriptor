"""Downloadable exports: plain text and PDF, one endpoint each."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session as DBSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.glossary import GlossaryTerm
from app.models.lecture import Lecture
from app.models.user import User
from app.schemas.glossary import GlossaryTermResponse
from app.schemas.summary import SummaryDocument
from app.services import export_pdf, export_txt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lectures/{lecture_id}/export", tags=["export"])


_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9]+")


def _get_owned_lecture(db: DBSession, lecture_id: int, user_id: int) -> Lecture:
    lecture = (
        db.query(Lecture)
        .filter(Lecture.id == lecture_id, Lecture.user_id == user_id)
        .first()
    )
    if lecture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lecture not found")
    return lecture


def _safe_stem(original_filename: str) -> str:
    stem = Path(original_filename).stem
    cleaned = _SAFE_STEM_RE.sub("_", stem).strip("_")
    return cleaned or "lecture"


def _collect_exports(
    db: DBSession, lecture: Lecture
) -> tuple[SummaryDocument | None, list[GlossaryTermResponse]]:
    summary_doc: SummaryDocument | None = None
    if lecture.summary is not None:
        summary_doc = SummaryDocument.model_validate_json(lecture.summary.content_json)
    terms = (
        db.query(GlossaryTerm)
        .filter(GlossaryTerm.lecture_id == lecture.id)
        .order_by(GlossaryTerm.first_mention_seconds, GlossaryTerm.id)
        .all()
    )
    entries = [GlossaryTermResponse.model_validate(t) for t in terms]
    return summary_doc, entries


@router.get("/txt")
def export_txt_endpoint(
    lecture_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> Response:
    """Return the lecture as a plain-text download."""
    lecture = _get_owned_lecture(db, lecture_id, user.id)
    summary_doc, entries = _collect_exports(db, lecture)
    text = export_txt.render_txt(lecture, lecture.transcript, summary_doc, entries or None)
    stem = _safe_stem(lecture.original_filename)
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{stem}.txt"'},
    )


@router.get("/pdf")
def export_pdf_endpoint(
    lecture_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> Response:
    """Return the lecture as a PDF download. 503 if the Cyrillic font is missing."""
    lecture = _get_owned_lecture(db, lecture_id, user.id)
    summary_doc, entries = _collect_exports(db, lecture)
    try:
        pdf_bytes = export_pdf.render_pdf(lecture, lecture.transcript, summary_doc, entries or None)
    except RuntimeError as exc:
        logger.exception("PDF export failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PDF export unavailable: the DejaVu Sans TTF font is missing. "
                "See the README for the font installation step."
            ),
        ) from exc
    stem = _safe_stem(lecture.original_filename)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'},
    )
