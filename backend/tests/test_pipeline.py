"""Pipeline unit tests: run `process_lecture` inline with mocked ASR/LLM/ffmpeg."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def _make_course(user_id: int, title: str = "Test course") -> int:
    from app.core.db import SessionLocal
    from app.models.course import Course

    db = SessionLocal()
    try:
        c = Course(user_id=user_id, title=title)
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id
    finally:
        db.close()


def _make_lecture(user_id: int, course_id: int) -> int:
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture, LectureStatus

    db = SessionLocal()
    try:
        lect = Lecture(
            course_id=course_id,
            user_id=user_id,
            title="Fixture lecture",
            original_filename="lecture.wav",
            duration_seconds=10.0,
            status=LectureStatus.queued,
        )
        db.add(lect)
        db.commit()
        db.refresh(lect)
        return lect.id
    finally:
        db.close()


def _save_original(user_id: int, lecture_id: int, wav_bytes: bytes) -> Path:
    from app.core.config import get_settings

    media_dir = get_settings().media_root / str(user_id) / str(lecture_id)
    media_dir.mkdir(parents=True, exist_ok=True)
    dst = media_dir / "original.wav"
    dst.write_bytes(wav_bytes)
    return dst


def _reload_lecture(lecture_id: int) -> Any:
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture

    db = SessionLocal()
    try:
        return db.get(Lecture, lecture_id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_pipeline_end_to_end_with_mocked_asr_and_llm(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    fake_ffmpeg: None,
    mock_asr: None,
    mock_llm: None,
) -> None:
    _, _, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id)
    _save_original(user_id, lecture_id, wav_bytes)

    from app.models.lecture import LectureStatus
    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    lecture = _reload_lecture(lecture_id)
    assert lecture is not None
    assert lecture.status == LectureStatus.completed, (
        f"expected completed, got {lecture.status} — {lecture.error_message}"
    )
    assert lecture.finished_at is not None
    assert lecture.error_message is None
    assert lecture.language == "uk"

    # Transcript segments persisted.
    from app.core.db import SessionLocal
    from app.models.glossary import GlossaryTerm
    from app.models.summary import Summary
    from app.models.transcript import Transcript, TranscriptSegment
    from app.schemas.summary import SummaryDocument

    db = SessionLocal()
    try:
        transcript = (
            db.query(Transcript).filter(Transcript.lecture_id == lecture_id).one()
        )
        segments = (
            db.query(TranscriptSegment)
            .filter(TranscriptSegment.transcript_id == transcript.id)
            .order_by(TranscriptSegment.index)
            .all()
        )
        assert len(segments) == 2
        assert segments[0].start_seconds == 0.0
        assert segments[1].end_seconds == 10.0

        summary = db.query(Summary).filter(Summary.lecture_id == lecture_id).one()
        doc = SummaryDocument.model_validate_json(summary.content_json)
        assert doc.title
        assert doc.sections

        terms = (
            db.query(GlossaryTerm).filter(GlossaryTerm.lecture_id == lecture_id).all()
        )
        assert len(terms) >= 10  # schema enforces min_length=10
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Failure paths.
# ---------------------------------------------------------------------------


def test_pipeline_asr_failure_marks_failed(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    fake_ffmpeg: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id)
    _save_original(user_id, lecture_id, wav_bytes)

    from app.services import asr as asr_module

    def _boom(_path: Path, **_kwargs: Any) -> None:
        raise asr_module.AsrError("simulated whisper explosion")

    monkeypatch.setattr(asr_module, "transcribe", _boom)

    from app.models.lecture import LectureStatus
    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.failed
    assert lecture.error_message is not None
    assert "simulated whisper explosion" in lecture.error_message


def test_pipeline_llm_failure_marks_failed(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    fake_ffmpeg: None,
    mock_asr: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id)
    _save_original(user_id, lecture_id, wav_bytes)

    from app.services import glossary_generator, llm as llm_module, summary_generator

    def _boom(_prompt: str, *, schema: Any, model: str | None = None) -> Any:
        raise llm_module.LlmGenerationError("simulated LLM outage")

    # Patch source module + both bound names in the generator modules (they do
    # `from app.services.llm import generate_json` at import time).
    monkeypatch.setattr(llm_module, "generate_json", _boom)
    monkeypatch.setattr(summary_generator, "generate_json", _boom)
    monkeypatch.setattr(glossary_generator, "generate_json", _boom)

    from app.models.lecture import LectureStatus
    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.failed
    assert lecture.error_message is not None
    assert "simulated LLM outage" in lecture.error_message
