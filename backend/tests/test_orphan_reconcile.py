"""Orphan reconciliation: derive checkpoint from disk/DB, then flip to failed.

The reconciler runs on server startup for any lecture stuck in a non-terminal
status. Wave 4 preserves partial artifacts by writing the highest matching stage
checkpoint before marking the row as failed, so the user can retry from there.
These tests exercise the disk/DB tiering directly by calling
``worker.reconcile_orphaned_lectures()`` — no HTTP roundtrip.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers — self-contained (do NOT modify conftest.py).
# ---------------------------------------------------------------------------


def _make_user(username: str, password: str = "password123") -> int:
    """Insert a user directly and return its id."""
    from app.core.db import SessionLocal
    from app.core.security import hash_password
    from app.models.user import User

    db = SessionLocal()
    try:
        u = User(username=username, password_hash=hash_password(password))
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _make_course(user_id: int, title: str = "Reconcile course") -> int:
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


def _make_lecture(
    user_id: int,
    course_id: int,
    status: Any,
    *,
    last_completed_stage: Any = None,
    error_message: str | None = None,
) -> int:
    """Insert a lecture row with the requested status/checkpoint/error."""
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture

    db = SessionLocal()
    try:
        lect = Lecture(
            course_id=course_id,
            user_id=user_id,
            title="Reconcile fixture",
            original_filename="fixture.wav",
            duration_seconds=10.0,
            status=status,
            last_completed_stage=last_completed_stage,
            error_message=error_message,
        )
        db.add(lect)
        db.commit()
        db.refresh(lect)
        return lect.id
    finally:
        db.close()


def _write_normalized_wav(user_id: int, lecture_id: int) -> Path:
    """Write a placeholder ``normalized.wav`` under the canonical media path."""
    from app.core.config import get_settings

    media_dir = get_settings().media_root / str(user_id) / str(lecture_id)
    media_dir.mkdir(parents=True, exist_ok=True)
    dst = media_dir / "normalized.wav"
    # Content is irrelevant to the reconciler — only ``Path.exists()`` is probed.
    dst.write_bytes(b"\x00" * 32)
    return dst


def _insert_transcript(lecture_id: int) -> None:
    from app.core.db import SessionLocal
    from app.models.transcript import Transcript, TranscriptSegment

    db = SessionLocal()
    try:
        transcript = Transcript(
            lecture_id=lecture_id,
            full_text="Full transcript text.",
            language="uk",
        )
        db.add(transcript)
        db.flush()
        db.add(
            TranscriptSegment(
                transcript_id=transcript.id,
                index=0,
                start_seconds=0.0,
                end_seconds=10.0,
                text="Full transcript text.",
                confidence=-0.2,
            )
        )
        db.commit()
    finally:
        db.close()


def _insert_summary(lecture_id: int) -> None:
    from app.core.db import SessionLocal
    from app.models.summary import Summary
    from app.schemas.summary import SummaryDocument, SummarySection

    doc = SummaryDocument(
        title="Reconcile fixture",
        language="uk",
        sections=[
            SummarySection(
                heading="Intro",
                timestamp_seconds=0.0,
                bullets=["Point A"],
            )
        ],
    )
    db = SessionLocal()
    try:
        db.add(
            Summary(
                lecture_id=lecture_id,
                content_json=doc.model_dump_json(),
                prompt_version="summary_v1",
                generation_language="uk",
            )
        )
        db.commit()
    finally:
        db.close()


def _insert_glossary_term(lecture_id: int, term: str = "Термін 1") -> None:
    """Insert a single glossary row. Existence is all the reconciler probes."""
    from app.core.db import SessionLocal
    from app.models.glossary import GlossaryTerm

    db = SessionLocal()
    try:
        db.add(
            GlossaryTerm(
                lecture_id=lecture_id,
                term=term,
                definition="Test definition.",
                first_mention_seconds=1.0,
                prompt_version="glossary_v1",
                generation_language="uk",
            )
        )
        db.commit()
    finally:
        db.close()


def _reload_lecture(lecture_id: int) -> Any:
    """Reload with a FRESH session — the reconciler's own session is already closed."""
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture

    db = SessionLocal()
    try:
        return db.get(Lecture, lecture_id)
    finally:
        db.close()


def _bootstrap(prefix: str) -> tuple[int, int]:
    """Create a fresh user + course, return ``(user_id, course_id)``."""
    user_id = _make_user(prefix + "_" + secrets.token_hex(4))
    course_id = _make_course(user_id)
    return user_id, course_id


# ---------------------------------------------------------------------------
# T1 — Legacy row without artifacts: NULL checkpoint, still marked failed.
# ---------------------------------------------------------------------------


def test_legacy_row_without_artifacts_stays_null_and_fails(client: Any) -> None:
    from app.models.lecture import LectureStatus
    from app.workers import worker
    from app.workers.worker import ORPHAN_RESTART_MESSAGE

    user_id, course_id = _bootstrap("t1")
    lecture_id = _make_lecture(user_id, course_id, LectureStatus.transcribing)

    reconciled = worker.reconcile_orphaned_lectures()
    assert reconciled == 1

    lecture = _reload_lecture(lecture_id)
    assert lecture is not None
    assert lecture.status == LectureStatus.failed
    assert lecture.last_completed_stage is None
    assert lecture.error_message == ORPHAN_RESTART_MESSAGE
    assert lecture.finished_at is not None


# ---------------------------------------------------------------------------
# T2 — Only normalized.wav on disk.
# ---------------------------------------------------------------------------


def test_only_normalized_wav_on_disk_gives_normalize_checkpoint(client: Any) -> None:
    from app.models.lecture import LectureStatus, StageCheckpoint
    from app.workers import worker
    from app.workers.worker import ORPHAN_RESTART_MESSAGE

    user_id, course_id = _bootstrap("t2")
    lecture_id = _make_lecture(user_id, course_id, LectureStatus.transcribing)
    _write_normalized_wav(user_id, lecture_id)

    reconciled = worker.reconcile_orphaned_lectures()
    assert reconciled == 1

    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.failed
    assert lecture.last_completed_stage == StageCheckpoint.normalize
    assert lecture.error_message == ORPHAN_RESTART_MESSAGE
    assert lecture.finished_at is not None


# ---------------------------------------------------------------------------
# T3 — Transcript row committed.
# ---------------------------------------------------------------------------


def test_committed_transcript_gives_transcribe_checkpoint(client: Any) -> None:
    from app.models.lecture import LectureStatus, StageCheckpoint
    from app.workers import worker
    from app.workers.worker import ORPHAN_RESTART_MESSAGE

    user_id, course_id = _bootstrap("t3")
    lecture_id = _make_lecture(user_id, course_id, LectureStatus.generating)
    _insert_transcript(lecture_id)

    reconciled = worker.reconcile_orphaned_lectures()
    assert reconciled == 1

    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.failed
    assert lecture.last_completed_stage == StageCheckpoint.transcribe
    assert lecture.error_message == ORPHAN_RESTART_MESSAGE


# ---------------------------------------------------------------------------
# T4 — Transcript + Summary committed.
# ---------------------------------------------------------------------------


def test_transcript_plus_summary_gives_summary_checkpoint(client: Any) -> None:
    from app.models.lecture import LectureStatus, StageCheckpoint
    from app.workers import worker
    from app.workers.worker import ORPHAN_RESTART_MESSAGE

    user_id, course_id = _bootstrap("t4")
    lecture_id = _make_lecture(user_id, course_id, LectureStatus.generating)
    _insert_transcript(lecture_id)
    _insert_summary(lecture_id)

    reconciled = worker.reconcile_orphaned_lectures()
    assert reconciled == 1

    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.failed
    assert lecture.last_completed_stage == StageCheckpoint.summary
    assert lecture.error_message == ORPHAN_RESTART_MESSAGE


# ---------------------------------------------------------------------------
# T5 — Idempotency across repeat runs.
# ---------------------------------------------------------------------------


def test_reconcile_is_idempotent_across_runs(client: Any) -> None:
    from app.models.lecture import LectureStatus, StageCheckpoint
    from app.workers import worker

    user_id, course_id = _bootstrap("t5")
    lecture_id = _make_lecture(user_id, course_id, LectureStatus.generating)
    _insert_transcript(lecture_id)

    first = worker.reconcile_orphaned_lectures()
    assert first == 1

    after_first = _reload_lecture(lecture_id)
    assert after_first.status == LectureStatus.failed
    assert after_first.last_completed_stage == StageCheckpoint.transcribe
    first_error = after_first.error_message
    first_finished_at = after_first.finished_at
    assert first_finished_at is not None

    second = worker.reconcile_orphaned_lectures()
    # Row is now `failed` (terminal); the filter excludes it, so nothing to reconcile.
    assert second == 0

    after_second = _reload_lecture(lecture_id)
    assert after_second.status == LectureStatus.failed
    assert after_second.last_completed_stage == StageCheckpoint.transcribe
    assert after_second.error_message == first_error
    assert after_second.finished_at == first_finished_at


# ---------------------------------------------------------------------------
# T6 — Defensive glossary branch: full artifact set flips to completed.
# ---------------------------------------------------------------------------


def test_generating_with_full_glossary_is_reconciled_to_completed(client: Any) -> None:
    from app.models.lecture import LectureStatus, StageCheckpoint
    from app.workers import worker

    user_id, course_id = _bootstrap("t6")
    lecture_id = _make_lecture(
        user_id,
        course_id,
        LectureStatus.generating,
        error_message="stale error from previous run",
    )
    _insert_transcript(lecture_id)
    _insert_summary(lecture_id)
    _insert_glossary_term(lecture_id)

    reconciled = worker.reconcile_orphaned_lectures()
    assert reconciled == 1

    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.completed
    # Wave 2b clears the stale error string when flipping a glossary orphan to completed.
    assert lecture.error_message is None
    assert lecture.last_completed_stage == StageCheckpoint.glossary
    assert lecture.finished_at is not None


# ---------------------------------------------------------------------------
# T7 — Pre-existing terminal `failed` row is NOT re-touched.
# ---------------------------------------------------------------------------


def test_pre_existing_failed_row_is_not_re_touched(client: Any) -> None:
    from app.models.lecture import LectureStatus
    from app.workers import worker

    user_id, course_id = _bootstrap("t7")
    lecture_id = _make_lecture(
        user_id,
        course_id,
        LectureStatus.failed,
        last_completed_stage=None,
        error_message="some old message",
    )

    reconciled = worker.reconcile_orphaned_lectures()
    assert reconciled == 0

    lecture = _reload_lecture(lecture_id)
    assert lecture.status == LectureStatus.failed
    assert lecture.last_completed_stage is None
    assert lecture.error_message == "some old message"
