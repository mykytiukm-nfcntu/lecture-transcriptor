"""Pipeline unit tests: run `process_lecture` inline with mocked ASR/LLM/ffmpeg."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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
    assert (
        lecture.status == LectureStatus.completed
    ), f"expected completed, got {lecture.status} — {lecture.error_message}"
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
        transcript = db.query(Transcript).filter(Transcript.lecture_id == lecture_id).one()
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

        terms = db.query(GlossaryTerm).filter(GlossaryTerm.lecture_id == lecture_id).all()
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

    from app.services import glossary_generator, summary_generator
    from app.services import llm as llm_module

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


# ---------------------------------------------------------------------------
# Checkpoint persistence on partial failure.
# ---------------------------------------------------------------------------


def test_pipeline_summary_failure_preserves_transcript_and_checkpoint(
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

    from app.services import llm as llm_module
    from app.services import summary_generator

    def _boom(_prompt: str, *, schema: Any, model: str | None = None) -> Any:
        raise llm_module.LlmGenerationError("simulated summary outage")

    # Only the summary stage should blow up; glossary must never run at all.
    monkeypatch.setattr(summary_generator, "generate_json", _boom)

    from app.models.lecture import LectureStatus, StageCheckpoint
    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.failed
    assert lecture.error_message is not None
    assert "simulated summary outage" in lecture.error_message
    assert lecture.last_completed_stage == StageCheckpoint.transcribe

    from app.core.db import SessionLocal
    from app.models.glossary import GlossaryTerm
    from app.models.summary import Summary
    from app.models.transcript import Transcript, TranscriptSegment

    db = SessionLocal()
    try:
        transcript = db.query(Transcript).filter(Transcript.lecture_id == lecture_id).one()
        segments = (
            db.query(TranscriptSegment)
            .filter(TranscriptSegment.transcript_id == transcript.id)
            .all()
        )
        assert len(segments) == 2
        assert db.query(Summary).filter(Summary.lecture_id == lecture_id).count() == 0
        assert db.query(GlossaryTerm).filter(GlossaryTerm.lecture_id == lecture_id).count() == 0
    finally:
        db.close()


def test_pipeline_glossary_failure_preserves_summary_and_checkpoint(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    fake_ffmpeg: None,
    mock_asr: None,
    mock_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id)
    _save_original(user_id, lecture_id, wav_bytes)

    from app.services import glossary_generator
    from app.services import llm as llm_module

    def _boom(_prompt: str, *, schema: Any, model: str | None = None) -> Any:
        raise llm_module.LlmGenerationError("simulated glossary outage")

    # `mock_llm` already stubbed all three modules; override only the glossary one.
    monkeypatch.setattr(glossary_generator, "generate_json", _boom)

    from app.models.lecture import LectureStatus, StageCheckpoint
    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.failed
    assert lecture.error_message is not None
    assert "simulated glossary outage" in lecture.error_message
    assert lecture.last_completed_stage == StageCheckpoint.summary

    from app.core.db import SessionLocal
    from app.models.glossary import GlossaryTerm
    from app.models.summary import Summary
    from app.models.transcript import Transcript, TranscriptSegment

    db = SessionLocal()
    try:
        transcript = db.query(Transcript).filter(Transcript.lecture_id == lecture_id).one()
        segments = (
            db.query(TranscriptSegment)
            .filter(TranscriptSegment.transcript_id == transcript.id)
            .all()
        )
        assert len(segments) == 2
        assert db.query(Summary).filter(Summary.lecture_id == lecture_id).count() == 1
        assert db.query(GlossaryTerm).filter(GlossaryTerm.lecture_id == lecture_id).count() == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Retry-from-checkpoint scenarios.
# ---------------------------------------------------------------------------


def _seed_transcript(lecture_id: int) -> None:
    """Insert a two-segment Ukrainian transcript that mirrors what `mock_asr` would emit."""
    from app.core.db import SessionLocal
    from app.models.transcript import Transcript, TranscriptSegment

    db = SessionLocal()
    try:
        transcript = Transcript(
            lecture_id=lecture_id,
            full_text="Тест",
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
                text="Перший сегмент.",
                confidence=-0.2,
            )
        )
        db.add(
            TranscriptSegment(
                transcript_id=transcript.id,
                index=1,
                start_seconds=5.0,
                end_seconds=10.0,
                text="Другий сегмент.",
                confidence=-0.2,
            )
        )
        db.commit()
    finally:
        db.close()


def _set_checkpoint(lecture_id: int, checkpoint: Any, *, language: str = "uk") -> None:
    """Force a lecture into `(status=failed, last_completed_stage=<checkpoint>)`."""
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture, LectureStatus

    db = SessionLocal()
    try:
        lecture = db.get(Lecture, lecture_id)
        assert lecture is not None
        lecture.status = LectureStatus.failed
        lecture.last_completed_stage = checkpoint
        lecture.language = language
        lecture.error_message = "prior failure"
        db.commit()
    finally:
        db.close()


def test_pipeline_retry_from_transcribe_skips_asr_and_completes(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    fake_ffmpeg: None,
    mock_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id)
    _save_original(user_id, lecture_id, wav_bytes)

    _seed_transcript(lecture_id)

    from app.models.lecture import LectureStatus, StageCheckpoint

    _set_checkpoint(lecture_id, StageCheckpoint.transcribe)

    from app.services import asr as asr_module

    # Any ASR invocation on this path is a bug; wrap the assertion in the mock's side_effect.
    mock_transcribe = MagicMock(
        side_effect=AssertionError("ASR must not run on retry from transcribe")
    )
    monkeypatch.setattr(asr_module, "transcribe", mock_transcribe)

    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    lecture = _reload_lecture(lecture_id)
    assert (
        lecture.status == LectureStatus.completed
    ), f"expected completed, got {lecture.status} — {lecture.error_message}"
    assert lecture.last_completed_stage == StageCheckpoint.glossary
    assert mock_transcribe.call_count == 0

    from app.core.db import SessionLocal
    from app.models.glossary import GlossaryTerm
    from app.models.summary import Summary

    db = SessionLocal()
    try:
        assert db.query(Summary).filter(Summary.lecture_id == lecture_id).count() == 1
        assert db.query(GlossaryTerm).filter(GlossaryTerm.lecture_id == lecture_id).count() >= 10
    finally:
        db.close()


def test_pipeline_retry_from_summary_skips_summary_generator(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    fake_ffmpeg: None,
    mock_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id)
    _save_original(user_id, lecture_id, wav_bytes)

    _seed_transcript(lecture_id)

    from app.core.db import SessionLocal
    from app.models.summary import Summary
    from app.schemas.summary import SummaryDocument, SummarySection

    preexisting_doc = SummaryDocument(
        title="Preexisting summary",
        language="uk",
        sections=[
            SummarySection(
                heading="Раніше згенеровано",
                timestamp_seconds=0.0,
                bullets=["Ця секція вже була в БД до retry."],
            )
        ],
    )
    db = SessionLocal()
    try:
        db.add(
            Summary(
                lecture_id=lecture_id,
                content_json=preexisting_doc.model_dump_json(),
                prompt_version="summary_v1",
                generation_language="uk",
            )
        )
        db.commit()
        preexisting_summary_id = (
            db.query(Summary.id).filter(Summary.lecture_id == lecture_id).scalar()
        )
    finally:
        db.close()

    from app.models.lecture import LectureStatus, StageCheckpoint

    _set_checkpoint(lecture_id, StageCheckpoint.summary)

    from app.services import asr as asr_module
    from app.services import summary_generator

    mock_transcribe = MagicMock(
        side_effect=AssertionError("ASR must not run on retry from summary")
    )
    mock_summary_generate = MagicMock(
        side_effect=AssertionError("summary generator must not run on retry from summary")
    )
    monkeypatch.setattr(asr_module, "transcribe", mock_transcribe)
    monkeypatch.setattr(summary_generator, "generate_json", mock_summary_generate)

    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    lecture = _reload_lecture(lecture_id)
    assert (
        lecture.status == LectureStatus.completed
    ), f"expected completed, got {lecture.status} — {lecture.error_message}"
    assert lecture.last_completed_stage == StageCheckpoint.glossary
    assert mock_transcribe.call_count == 0
    assert mock_summary_generate.call_count == 0

    from app.models.glossary import GlossaryTerm

    db = SessionLocal()
    try:
        terms = db.query(GlossaryTerm).filter(GlossaryTerm.lecture_id == lecture_id).all()
        assert len(terms) >= 10

        summary = db.query(Summary).filter(Summary.lecture_id == lecture_id).one()
        assert summary.id == preexisting_summary_id
    finally:
        db.close()


def test_pipeline_retry_deletes_leftover_summary_before_regenerating(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    fake_ffmpeg: None,
    mock_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id)
    _save_original(user_id, lecture_id, wav_bytes)

    _seed_transcript(lecture_id)

    from app.core.db import SessionLocal
    from app.models.summary import Summary
    from app.schemas.summary import SummaryDocument, SummarySection

    # Leftover summary that a prior crash left behind without advancing the checkpoint.
    # Use a distinctive title so we can distinguish it from what `mock_llm` regenerates.
    leftover_title = "Leftover summary from previous crash"
    leftover_doc = SummaryDocument(
        title=leftover_title,
        language="uk",
        sections=[
            SummarySection(
                heading="Stale",
                timestamp_seconds=0.0,
                bullets=["Не має бути видимим після retry."],
            )
        ],
    )
    db = SessionLocal()
    try:
        db.add(
            Summary(
                lecture_id=lecture_id,
                content_json=leftover_doc.model_dump_json(),
                prompt_version="leftover",
                generation_language="uk",
            )
        )
        db.commit()
        leftover_summary_id = db.query(Summary.id).filter(Summary.lecture_id == lecture_id).scalar()
    finally:
        db.close()

    from app.models.lecture import LectureStatus, StageCheckpoint

    _set_checkpoint(lecture_id, StageCheckpoint.transcribe)

    from app.services import asr as asr_module

    mock_transcribe = MagicMock(
        side_effect=AssertionError("ASR must not run on retry from transcribe")
    )
    monkeypatch.setattr(asr_module, "transcribe", mock_transcribe)

    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    lecture = _reload_lecture(lecture_id)
    assert (
        lecture.status == LectureStatus.completed
    ), f"expected completed, got {lecture.status} — {lecture.error_message}"

    # Exactly one Summary row survives, and it is the regenerated one (leftover title gone).
    # SQLite reuses ROWIDs when a table is emptied without AUTOINCREMENT, so comparing by
    # `id` is unreliable here; we compare by prompt_version + content_json instead.
    db = SessionLocal()
    try:
        remaining = db.query(Summary).filter(Summary.lecture_id == lecture_id).all()
        assert len(remaining) == 1
        assert remaining[0].prompt_version != "leftover"
        assert leftover_title not in remaining[0].content_json
        # `id` comparison kept as a soft check: on backends with strict monotonic PKs it
        # would also differ, but on SQLite it may coincide — the content check above is
        # authoritative.
        _ = leftover_summary_id
    finally:
        db.close()


def test_pipeline_retry_with_failing_summary_preserves_leftover_summary(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    fake_ffmpeg: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure-path sibling to `..._deletes_leftover_summary_before_regenerating`.

    Wave R1a reordered the leftover-Summary delete to run only AFTER the generator
    succeeds; this test locks that invariant in by asserting the leftover row survives
    when the summary generator raises.
    """
    _, _, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id)
    _save_original(user_id, lecture_id, wav_bytes)

    # Retry-from-transcribe implies both normalize and transcribe already ran successfully,
    # so `normalized.wav` must exist on disk — otherwise the pipeline re-runs normalise and
    # regresses `last_completed_stage` back to `normalize`.
    from app.core.config import get_settings

    media_dir = get_settings().media_root / str(user_id) / str(lecture_id)
    (media_dir / "normalized.wav").write_bytes(wav_bytes)

    _seed_transcript(lecture_id)

    from app.core.db import SessionLocal
    from app.models.summary import Summary
    from app.schemas.summary import SummaryDocument, SummarySection

    leftover_doc = SummaryDocument(
        title="Leftover marker summary",
        language="uk",
        sections=[
            SummarySection(
                heading="Marker",
                timestamp_seconds=0.0,
                bullets=["Ця секція має пережити failed regeneration."],
            )
        ],
    )
    db = SessionLocal()
    try:
        leftover = Summary(
            lecture_id=lecture_id,
            content_json=leftover_doc.model_dump_json(),
            prompt_version="leftover_marker",
            generation_language="uk",
        )
        db.add(leftover)
        db.commit()
        db.refresh(leftover)
        leftover_summary_id = leftover.id
    finally:
        db.close()

    from app.models.lecture import LectureStatus, StageCheckpoint

    _set_checkpoint(lecture_id, StageCheckpoint.transcribe)

    from app.services import asr as asr_module
    from app.services import llm as llm_module
    from app.services import summary_generator

    def boom(_prompt: str, *, schema: Any, model: str | None = None) -> Any:
        raise llm_module.LlmGenerationError("simulated summary failure")

    monkeypatch.setattr(summary_generator, "generate_json", boom)
    monkeypatch.setattr(
        asr_module,
        "transcribe",
        MagicMock(side_effect=AssertionError("ASR must not run")),
    )

    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.failed
    assert lecture.error_message is not None
    assert "simulated summary failure" in lecture.error_message
    # Checkpoint is preserved at the pre-summary stage — the failure did not advance it.
    assert lecture.last_completed_stage == StageCheckpoint.transcribe

    from app.models.glossary import GlossaryTerm
    from app.models.transcript import Transcript, TranscriptSegment

    db = SessionLocal()
    try:
        transcript = db.query(Transcript).filter(Transcript.lecture_id == lecture_id).one()
        segments = (
            db.query(TranscriptSegment)
            .filter(TranscriptSegment.transcript_id == transcript.id)
            .all()
        )
        assert len(segments) == 2

        # The Wave R1a invariant: leftover Summary row is untouched by a failed regeneration.
        remaining = db.query(Summary).filter(Summary.lecture_id == lecture_id).all()
        assert (
            len(remaining) == 1
        ), "no Summary row found — Wave R1a leftover-preservation fix regressed"
        assert remaining[0].id == leftover_summary_id
        assert remaining[0].prompt_version == "leftover_marker"

        assert db.query(GlossaryTerm).filter(GlossaryTerm.lecture_id == lecture_id).count() == 0
    finally:
        db.close()


def test_pipeline_retry_from_normalize_reuses_existing_normalized_wav(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    mock_asr: None,
    mock_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id)

    # Both files must be on disk: the retry-from-normalize path is
    # `_reached(normalize) AND normalized.wav exists` → skip ffmpeg.
    _save_original(user_id, lecture_id, wav_bytes)

    from app.core.config import get_settings

    media_dir = get_settings().media_root / str(user_id) / str(lecture_id)
    normalized_path = media_dir / "normalized.wav"
    normalized_path.write_bytes(b"pretend-normalized-wav-bytes")

    from app.models.lecture import LectureStatus, StageCheckpoint

    _set_checkpoint(lecture_id, StageCheckpoint.normalize)

    from app.services import media as media_svc

    mock_normalise = MagicMock(
        side_effect=AssertionError("normalise must not run when normalized.wav exists")
    )
    monkeypatch.setattr(media_svc, "normalise", mock_normalise)

    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    lecture = _reload_lecture(lecture_id)
    assert (
        lecture.status == LectureStatus.completed
    ), f"expected completed, got {lecture.status} — {lecture.error_message}"
    assert mock_normalise.call_count == 0


def test_pipeline_retry_from_glossary_returns_early(
    authed_client: tuple[Any, str, int],
) -> None:
    _, _, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id)

    _seed_transcript(lecture_id)

    from app.core.db import SessionLocal
    from app.models.glossary import GlossaryTerm
    from app.models.summary import Summary
    from app.schemas.summary import SummaryDocument, SummarySection

    summary_doc = SummaryDocument(
        title="Previously completed",
        language="uk",
        sections=[
            SummarySection(
                heading="Intro",
                timestamp_seconds=0.0,
                bullets=["Full run had already produced this."],
            )
        ],
    )
    db = SessionLocal()
    try:
        db.add(
            Summary(
                lecture_id=lecture_id,
                content_json=summary_doc.model_dump_json(),
                prompt_version="summary_v1",
                generation_language="uk",
            )
        )
        for i in range(1, 11):
            db.add(
                GlossaryTerm(
                    lecture_id=lecture_id,
                    term=f"Термін {i}",
                    definition=f"Визначення {i}.",
                    first_mention_seconds=float(i),
                    prompt_version="glossary_v1",
                    generation_language="uk",
                )
            )
        db.commit()
        summary_id_before = db.query(Summary.id).filter(Summary.lecture_id == lecture_id).scalar()
        glossary_count_before = (
            db.query(GlossaryTerm).filter(GlossaryTerm.lecture_id == lecture_id).count()
        )
    finally:
        db.close()

    from app.models.lecture import Lecture, LectureStatus, StageCheckpoint

    _set_checkpoint(lecture_id, StageCheckpoint.glossary)

    # The defensive branch must not touch `error_message` or stamp `started_at`; pin both
    # to a known pre-run state so we can assert they survive the early-return unchanged.
    db = SessionLocal()
    try:
        lecture_row = db.get(Lecture, lecture_id)
        assert lecture_row is not None
        lecture_row.error_message = "prior failure"
        lecture_row.started_at = None
        db.commit()
    finally:
        db.close()

    from app.services import pipeline

    pipeline.process_lecture(lecture_id)

    # The pipeline's defensive branch logs an error and returns without touching the row,
    # so `status` stays `failed` and the checkpoint stays at `glossary`.
    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.failed
    assert lecture.last_completed_stage == StageCheckpoint.glossary
    assert lecture.error_message == "prior failure"
    assert lecture.started_at is None

    db = SessionLocal()
    try:
        summary_id_after = db.query(Summary.id).filter(Summary.lecture_id == lecture_id).scalar()
        glossary_count_after = (
            db.query(GlossaryTerm).filter(GlossaryTerm.lecture_id == lecture_id).count()
        )
        assert summary_id_after == summary_id_before
        assert glossary_count_after == glossary_count_before
    finally:
        db.close()
