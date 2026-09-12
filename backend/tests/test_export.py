"""Tests for /api/lectures/{id}/export/txt and /pdf."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _make_completed_lecture(user_id: int) -> tuple[int, int]:
    """Insert course + completed lecture + transcript + summary + glossary.

    Returns `(course_id, lecture_id)`.
    """
    from app.core.db import SessionLocal
    from app.models.course import Course
    from app.models.glossary import GlossaryTerm
    from app.models.lecture import Lecture, LectureStatus
    from app.models.summary import Summary
    from app.models.transcript import Transcript, TranscriptSegment
    from app.schemas.summary import (
        SummaryDocument,
        SummaryHighlight,
        SummarySection,
    )

    db = SessionLocal()
    try:
        course = Course(user_id=user_id, title="Export course")
        db.add(course)
        db.flush()

        lecture = Lecture(
            course_id=course.id,
            user_id=user_id,
            title="Export lecture",
            original_filename="export lecture.wav",
            duration_seconds=10.0,
            language="uk",
            status=LectureStatus.completed,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        db.add(lecture)
        db.flush()

        transcript = Transcript(
            lecture_id=lecture.id,
            full_text="First segment. Second segment.",
            language="uk",
        )
        db.add(transcript)
        db.flush()
        db.add(
            TranscriptSegment(
                transcript_id=transcript.id,
                index=0,
                start_seconds=0.0,
                end_seconds=5.0,
                text="Перший тестовий сегмент.",
                confidence=-0.2,
            )
        )
        db.add(
            TranscriptSegment(
                transcript_id=transcript.id,
                index=1,
                start_seconds=5.0,
                end_seconds=10.0,
                text="Другий тестовий сегмент.",
                confidence=-0.25,
            )
        )

        summary_doc = SummaryDocument(
            title="Export lecture",
            language="uk",
            sections=[
                SummarySection(
                    heading="Вступ до теми",
                    timestamp_seconds=0.0,
                    bullets=["Пункт номер один", "Пункт номер два"],
                    highlights=[
                        SummaryHighlight(
                            kind="definition",
                            text="Ключове визначення.",
                            timestamp_seconds=2.0,
                        )
                    ],
                )
            ],
        )
        db.add(
            Summary(
                lecture_id=lecture.id,
                content_json=summary_doc.model_dump_json(),
                prompt_version="summary_v1",
                generation_language="uk",
            )
        )
        for i in range(1, 11):
            db.add(
                GlossaryTerm(
                    lecture_id=lecture.id,
                    term=f"Термін {i}",
                    definition=f"Опис терміна {i}.",
                    first_mention_seconds=float(i),
                    prompt_version="glossary_v1",
                    generation_language="uk",
                )
            )
        db.commit()
        return course.id, lecture.id
    finally:
        db.close()


@pytest.fixture
def completed_lecture(authed_client: tuple[Any, str, int]) -> dict[str, Any]:
    client, token, user_id = authed_client
    course_id, lecture_id = _make_completed_lecture(user_id)
    return {
        "client": client,
        "token": token,
        "user_id": user_id,
        "course_id": course_id,
        "lecture_id": lecture_id,
    }


# ---------------------------------------------------------------------------
# TXT export.
# ---------------------------------------------------------------------------


def test_export_txt_contains_expected_sections(
    completed_lecture: dict[str, Any],
) -> None:
    ctx = completed_lecture
    resp = ctx["client"].get(
        f"/api/lectures/{ctx['lecture_id']}/export/txt",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/plain")
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition
    assert disposition.endswith('.txt"'), disposition

    body = resp.text
    assert body.startswith("# Export lecture")
    assert "## Summary" in body
    assert "## Glossary" in body
    assert "## Transcript" in body
    # Transcript segments echoed.
    assert "Перший тестовий сегмент." in body
    assert "Другий тестовий сегмент." in body
    # Summary heading + at least one bullet.
    assert "Вступ до теми" in body
    assert "Пункт номер один" in body
    # Glossary term echoed.
    assert "Термін 1" in body


# ---------------------------------------------------------------------------
# PDF export.
# ---------------------------------------------------------------------------


def test_export_pdf_returns_pdf_bytes(
    completed_lecture: dict[str, Any],
    dejavu_font: Path | None,
) -> None:
    if dejavu_font is None:
        pytest.skip("no network for font download")

    ctx = completed_lecture
    resp = ctx["client"].get(
        f"/api/lectures/{ctx['lecture_id']}/export/pdf",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.content.startswith(b"%PDF-"), resp.content[:12]
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition
    assert disposition.endswith('.pdf"'), disposition


def test_export_pdf_503_when_font_missing(
    completed_lecture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ctx = completed_lecture
    from app.services import export_pdf

    # Point the module at a TTF that doesn't exist and force re-registration
    # so the real `_ensure_font_registered -> RuntimeError -> 503` path runs.
    missing = tmp_path / "does-not-exist.ttf"
    monkeypatch.setattr(export_pdf, "_FONT_PATH", missing)
    monkeypatch.setattr(export_pdf, "_font_registered", False)

    resp = ctx["client"].get(
        f"/api/lectures/{ctx['lecture_id']}/export/pdf",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert "font" in body["detail"].lower()
