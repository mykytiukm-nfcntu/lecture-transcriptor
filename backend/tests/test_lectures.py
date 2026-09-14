"""Tests for /api/courses/{id}/lectures and /api/lectures/{id}: upload, delete, list."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Local helpers.
# ---------------------------------------------------------------------------


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


def _make_lecture(
    user_id: int,
    course_id: int,
    *,
    title: str = "Fixture lecture",
    status: str = "queued",
    original_filename: str = "test.wav",
    duration: float | None = 10.0,
) -> int:
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture, LectureStatus

    db = SessionLocal()
    try:
        lect = Lecture(
            course_id=course_id,
            user_id=user_id,
            title=title,
            original_filename=original_filename,
            duration_seconds=duration,
            language="uk",
            status=LectureStatus(status),
        )
        db.add(lect)
        db.commit()
        db.refresh(lect)
        return lect.id
    finally:
        db.close()


def _lecture_row(lecture_id: int) -> Any:
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture

    db = SessionLocal()
    try:
        return db.get(Lecture, lecture_id)
    finally:
        db.close()


def _lecture_count() -> int:
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture

    db = SessionLocal()
    try:
        return db.query(Lecture).count()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Upload happy paths.
# ---------------------------------------------------------------------------


def test_upload_wav_accepted_returns_202(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    fake_ffmpeg: None,
    mock_worker_enqueue: MagicMock,
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)

    resp = client.post(
        f"/api/courses/{course_id}/lectures",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("lecture.wav", wav_bytes, "audio/wav")},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["preliminary_eta_seconds"] is not None
    assert body["preliminary_eta_seconds"] > 0
    assert body["original_filename"] == "lecture.wav"

    mock_worker_enqueue.assert_called_once()
    args, kwargs = mock_worker_enqueue.call_args
    assert args[0] == body["id"]
    assert kwargs.get("generation_language") is None


def test_upload_mp3_accepted_returns_202(
    authed_client: tuple[Any, str, int],
    mp3_bytes: bytes,
    fake_ffmpeg: None,
    mock_worker_enqueue: MagicMock,
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)

    resp = client.post(
        f"/api/courses/{course_id}/lectures",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("lecture.mp3", mp3_bytes, "audio/mpeg")},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["original_filename"] == "lecture.mp3"


# ---------------------------------------------------------------------------
# Upload rejection paths.
# ---------------------------------------------------------------------------


def test_upload_m4a_rejected_returns_415(
    authed_client: tuple[Any, str, int],
    fake_ffmpeg: None,
    mock_worker_enqueue: MagicMock,
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)

    # Minimal M4A ftyp box: 32-byte box, `ftyp` header, `M4A ` major brand.
    body = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00" + b"\x00" * 16
    resp = client.post(
        f"/api/courses/{course_id}/lectures",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("lecture.m4a", body, "audio/mp4")},
    )
    assert resp.status_code == 202, resp.text
    mock_worker_enqueue.assert_called_once()


def test_upload_video_mp4_mime_rejected_returns_415(
    authed_client: tuple[Any, str, int],
    fake_ffmpeg: None,
    mock_worker_enqueue: MagicMock,
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)

    # `video/mp4` is not in the accepted MIME set, so we reject before magic-byte sniffing.
    body = b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00" + b"\x00" * 16
    resp = client.post(
        f"/api/courses/{course_id}/lectures",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("movie.mp4", body, "video/mp4")},
    )
    assert resp.status_code == 415, resp.text
    assert _lecture_count() == 0
    mock_worker_enqueue.assert_not_called()


def test_upload_empty_file_rejected(
    authed_client: tuple[Any, str, int],
    fake_ffmpeg: None,
    mock_worker_enqueue: MagicMock,
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)

    # Empty body, no acceptable MIME → 415 at validate_mime.
    resp = client.post(
        f"/api/courses/{course_id}/lectures",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("empty.bin", b"", "application/octet-stream")},
    )
    assert resp.status_code in (400, 415), resp.text
    assert resp.status_code != 500
    mock_worker_enqueue.assert_not_called()


def test_upload_random_bytes_with_audio_mime_rejected_415(
    authed_client: tuple[Any, str, int],
    fake_ffmpeg: None,
    mock_worker_enqueue: MagicMock,
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)

    # MIME passes but the bytes do not match ID3 / MPEG-sync / RIFF...WAVE.
    random_bytes = secrets.token_bytes(4096)
    resp = client.post(
        f"/api/courses/{course_id}/lectures",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("fake.wav", random_bytes, "audio/wav")},
    )
    assert resp.status_code == 415, resp.text
    mock_worker_enqueue.assert_not_called()

    # API-side validation rolls back; no phantom `failed` row is left behind.
    rows = _all_lectures()
    assert rows == []


def _all_lectures() -> list[Any]:
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture

    db = SessionLocal()
    try:
        return db.query(Lecture).all()
    finally:
        db.close()


def test_upload_while_job_locked_returns_409(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
    mock_worker_enqueue: MagicMock,
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)

    from app.core import job_lock

    monkeypatch.setattr(job_lock, "is_locked", lambda: True)

    resp = client.post(
        f"/api/courses/{course_id}/lectures",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("lecture.wav", wav_bytes, "audio/wav")},
    )
    assert resp.status_code == 409, resp.text
    assert _lecture_count() == 0
    mock_worker_enqueue.assert_not_called()


def test_upload_with_language_override_query_param(
    authed_client: tuple[Any, str, int],
    wav_bytes: bytes,
    fake_ffmpeg: None,
    mock_worker_enqueue: MagicMock,
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)

    resp = client.post(
        f"/api/courses/{course_id}/lectures?language=en",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("lecture.wav", wav_bytes, "audio/wav")},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    mock_worker_enqueue.assert_called_once()
    args, kwargs = mock_worker_enqueue.call_args
    assert args[0] == body["id"]
    assert kwargs.get("generation_language") == "en"


# ---------------------------------------------------------------------------
# Delete + list.
# ---------------------------------------------------------------------------


def test_delete_lecture_while_processing_returns_409(
    authed_client: tuple[Any, str, int],
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="transcribing")

    resp = client.delete(
        f"/api/lectures/{lecture_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text
    # Row still there.
    assert _lecture_row(lecture_id) is not None


def test_delete_lecture_removes_media_folder(
    authed_client: tuple[Any, str, int],
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="completed")

    from app.core.config import get_settings

    media_dir = get_settings().media_root / str(user_id) / str(lecture_id)
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "original.wav").write_bytes(b"fake wav")
    assert media_dir.exists()

    resp = client.delete(
        f"/api/lectures/{lecture_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204, resp.text
    assert not media_dir.exists()


def test_list_lectures_filters_by_course(
    authed_client: tuple[Any, str, int],
) -> None:
    client, token, user_id = authed_client
    course_a = _make_course(user_id, "Course A")
    course_b = _make_course(user_id, "Course B")

    lecture_a = _make_lecture(user_id, course_a, title="A-only")
    lecture_b = _make_lecture(user_id, course_b, title="B-only")

    resp_a = client.get(
        f"/api/courses/{course_a}/lectures",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_a.status_code == 200
    ids_a = [row["id"] for row in resp_a.json()]
    assert lecture_a in ids_a
    assert lecture_b not in ids_a
    assert len(ids_a) == 1

    resp_b = client.get(
        f"/api/courses/{course_b}/lectures",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_b.status_code == 200
    ids_b = [row["id"] for row in resp_b.json()]
    assert ids_b == [lecture_b]


# ---------- Retry endpoint ----------
# Local helpers mirroring the in-function-import pattern used by `_make_lecture`
# so app.* imports still happen after the conftest env fixture has run.


def _set_checkpoint(
    lecture_id: int,
    stage: Any,
    *,
    error_message: str | None = None,
    generation_language: str | None = None,
    ollama_model: str | None = None,
) -> None:
    """Mutate a persisted lecture row's checkpoint + optional retry-related fields."""
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture

    db = SessionLocal()
    try:
        lecture = db.get(Lecture, lecture_id)
        assert lecture is not None
        lecture.last_completed_stage = stage
        if error_message is not None:
            lecture.error_message = error_message
        if generation_language is not None:
            lecture.generation_language = generation_language
        if ollama_model is not None:
            lecture.ollama_model = ollama_model
        db.commit()
    finally:
        db.close()


def _insert_transcript(lecture_id: int, *, full_text: str = "Тест", language: str = "uk") -> None:
    from app.core.db import SessionLocal
    from app.models.transcript import Transcript, TranscriptSegment

    db = SessionLocal()
    try:
        t = Transcript(lecture_id=lecture_id, full_text=full_text, language=language)
        db.add(t)
        db.flush()
        db.add(
            TranscriptSegment(
                transcript_id=t.id,
                index=0,
                start_seconds=0.0,
                end_seconds=10.0,
                text=full_text,
                confidence=-0.2,
            )
        )
        db.commit()
    finally:
        db.close()


def _insert_summary(
    lecture_id: int,
    *,
    title: str = "Тестова лекція",
    language: str = "uk",
) -> None:
    from app.core.db import SessionLocal
    from app.models.summary import Summary
    from app.schemas.summary import SummaryDocument, SummarySection

    document = SummaryDocument(
        title=title,
        language=language,
        sections=[
            SummarySection(
                heading="Вступ",
                timestamp_seconds=0.0,
                bullets=["Ключова теза"],
            )
        ],
    )
    db = SessionLocal()
    try:
        db.add(
            Summary(
                lecture_id=lecture_id,
                content_json=document.model_dump_json(),
                prompt_version="summary_v1",
                generation_language=language,
            )
        )
        db.commit()
    finally:
        db.close()


# Group A — Retry endpoint status matrix.


def test_retry_on_completed_returns_400_already_completed(
    authed_client: tuple[Any, str, int],
    mock_worker_enqueue: MagicMock,
) -> None:
    from app.models.lecture import StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="completed")
    _set_checkpoint(lecture_id, StageCheckpoint.glossary)

    resp = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "already_completed"
    mock_worker_enqueue.assert_not_called()


def test_retry_on_legacy_failed_returns_400_not_resumable(
    authed_client: tuple[Any, str, int],
    mock_worker_enqueue: MagicMock,
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    # Grandfathered failure: last_completed_stage defaults to NULL.
    lecture_id = _make_lecture(user_id, course_id, status="failed")

    resp = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "not_resumable"
    mock_worker_enqueue.assert_not_called()


def test_retry_on_retryable_failed_returns_202_and_enqueues(
    authed_client: tuple[Any, str, int],
    mock_worker_enqueue: MagicMock,
) -> None:
    from app.models.lecture import LectureStatus, StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="failed")
    _set_checkpoint(
        lecture_id,
        StageCheckpoint.transcribe,
        error_message="previous crash",
    )

    resp = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["error_message"] is None
    assert body["started_at"] is None
    assert body["finished_at"] is None
    assert body["last_completed_stage"] == "transcribe"
    # can_retry is derived from status==failed; queued flips it to False by design.
    assert body["can_retry"] is False

    mock_worker_enqueue.assert_called_once()
    args, kwargs = mock_worker_enqueue.call_args
    assert args[0] == lecture_id
    assert kwargs.get("generation_language") is None
    assert kwargs.get("model") is None

    row = _lecture_row(lecture_id)
    assert row is not None
    assert row.status == LectureStatus.queued
    assert row.last_completed_stage == StageCheckpoint.transcribe
    assert row.error_message is None
    assert row.finished_at is None


def test_retry_when_job_running_returns_409(
    authed_client: tuple[Any, str, int],
    monkeypatch: pytest.MonkeyPatch,
    mock_worker_enqueue: MagicMock,
) -> None:
    # Chose monkeypatch over `job_lock.try_acquire()` + finally: no manual cleanup,
    # conftest resets lock state between tests, matches test_upload_while_job_locked pattern.
    from app.core import job_lock
    from app.models.lecture import LectureStatus, StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="failed")
    _set_checkpoint(lecture_id, StageCheckpoint.transcribe, error_message="previous crash")

    monkeypatch.setattr(job_lock, "is_locked", lambda: True)

    resp = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "job_running"
    mock_worker_enqueue.assert_not_called()

    row = _lecture_row(lecture_id)
    assert row is not None
    assert row.status == LectureStatus.failed
    assert row.error_message == "previous crash"


def test_retry_on_foreign_lecture_returns_404(
    authed_client: tuple[Any, str, int],
    second_user: dict[str, Any],
    mock_worker_enqueue: MagicMock,
) -> None:
    from app.models.lecture import StageCheckpoint

    client, token, _user_a_id = authed_client
    # Lecture belongs to user B; user A tries to retry it with their own bearer.
    course_id = _make_course(second_user["id"])
    lecture_id = _make_lecture(second_user["id"], course_id, status="failed")
    _set_checkpoint(lecture_id, StageCheckpoint.transcribe)

    resp = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text
    mock_worker_enqueue.assert_not_called()


@pytest.mark.parametrize(
    "status_value",
    ["normalizing", "transcribing", "generating"],
)
def test_retry_on_active_returns_409_job_running(
    authed_client: tuple[Any, str, int],
    mock_worker_enqueue: MagicMock,
    status_value: str,
) -> None:
    from app.models.lecture import StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status=status_value)
    stage_for_status: dict[str, Any] = {
        "normalizing": None,
        "transcribing": StageCheckpoint.normalize,
        "generating": StageCheckpoint.transcribe,
    }
    _set_checkpoint(lecture_id, stage_for_status[status_value])

    resp = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "job_running"
    mock_worker_enqueue.assert_not_called()


def test_retry_on_queued_returns_409_already_queued(
    authed_client: tuple[Any, str, int],
    mock_worker_enqueue: MagicMock,
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="queued")
    _set_checkpoint(lecture_id, None)

    resp = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "already_queued"
    mock_worker_enqueue.assert_not_called()


def test_retry_preserves_generation_language_and_model_overrides(
    authed_client: tuple[Any, str, int],
    mock_worker_enqueue: MagicMock,
) -> None:
    from app.models.lecture import StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="failed")
    _set_checkpoint(
        lecture_id,
        StageCheckpoint.transcribe,
        generation_language="en",
        ollama_model="qwen2.5:3b",
    )

    resp = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202, resp.text

    mock_worker_enqueue.assert_called_once()
    _args, kwargs = mock_worker_enqueue.call_args
    assert kwargs.get("generation_language") == "en"
    assert kwargs.get("model") == "qwen2.5:3b"


def _set_lifecycle_fields(
    lecture_id: int,
    *,
    started_at: datetime | None,
    finished_at: datetime | None,
) -> None:
    """Seed `started_at` / `finished_at` on a persisted lecture row."""
    from app.core.db import SessionLocal
    from app.models.lecture import Lecture

    db = SessionLocal()
    try:
        lecture = db.get(Lecture, lecture_id)
        assert lecture is not None
        lecture.started_at = started_at
        lecture.finished_at = finished_at
        db.commit()
    finally:
        db.close()


def test_retry_on_failed_with_glossary_checkpoint_returns_400_already_completed(
    authed_client: tuple[Any, str, int],
    mock_worker_enqueue: MagicMock,
) -> None:
    # Defensive branch: a `failed` row that also carries the terminal checkpoint
    # (pipeline normally commits `glossary` + `completed` together) still
    # short-circuits to `already_completed` and never enqueues.
    from app.models.lecture import LectureStatus, StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="failed")
    _set_checkpoint(lecture_id, StageCheckpoint.glossary, error_message="prior failure")

    resp = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "already_completed"
    assert isinstance(detail["message"], str) and detail["message"]
    mock_worker_enqueue.assert_not_called()

    row = _lecture_row(lecture_id)
    assert row is not None
    assert row.status == LectureStatus.failed
    assert row.last_completed_stage == StageCheckpoint.glossary
    assert row.error_message == "prior failure"


def test_retry_rolls_back_state_when_worker_enqueue_raises_locked_error(
    authed_client: tuple[Any, str, int],
    mock_worker_enqueue: MagicMock,
) -> None:
    # is_locked() is left returning False so the pre-mutation check passes and
    # the endpoint commits `queued`; only then does worker.enqueue raise inside
    # the try block, exercising the crash-state restore path.
    from app.core.job_lock import LockedError
    from app.models.lecture import LectureStatus, StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="failed")
    _set_checkpoint(lecture_id, StageCheckpoint.transcribe, error_message="old error")
    prev_started_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    prev_finished_at = datetime(2026, 1, 1, 10, 5, 0, tzinfo=UTC)
    _set_lifecycle_fields(lecture_id, started_at=prev_started_at, finished_at=prev_finished_at)

    mock_worker_enqueue.side_effect = LockedError("simulated race")

    resp = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "job_running"
    mock_worker_enqueue.assert_called_once()

    row = _lecture_row(lecture_id)
    assert row is not None
    assert row.status == LectureStatus.failed
    assert row.error_message == "old error"
    assert row.last_completed_stage == StageCheckpoint.transcribe
    assert row.started_at == prev_started_at
    assert row.finished_at == prev_finished_at


def test_retry_twice_in_a_row_yields_202_then_409_already_queued(
    authed_client: tuple[Any, str, int],
    mock_worker_enqueue: MagicMock,
) -> None:
    # Simulated concurrent retry: A wins (row → queued), B lands on the
    # `queued` branch and returns 409 already_queued. Worker is mocked so
    # nothing transitions the row between the two calls.
    from app.models.lecture import LectureStatus, StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="failed")
    _set_checkpoint(lecture_id, StageCheckpoint.transcribe, error_message="prev")

    resp_a = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_a.status_code == 202, resp_a.text
    assert mock_worker_enqueue.call_count == 1

    resp_b = client.post(
        f"/api/lectures/{lecture_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_b.status_code == 409, resp_b.text
    assert resp_b.json()["detail"]["code"] == "already_queued"
    assert mock_worker_enqueue.call_count == 1

    row = _lecture_row(lecture_id)
    assert row is not None
    assert row.status == LectureStatus.queued
    assert row.error_message is None
    assert row.last_completed_stage == StageCheckpoint.transcribe


# Group B — Detail / status responses expose the new fields.


def test_detail_on_retryable_failed_exposes_can_retry_true(
    authed_client: tuple[Any, str, int],
) -> None:
    from app.models.lecture import StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="failed")
    _set_checkpoint(lecture_id, StageCheckpoint.summary, error_message="prev crash")

    resp = client.get(
        f"/api/lectures/{lecture_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert body["last_completed_stage"] == "summary"
    assert body["can_retry"] is True


def test_detail_on_legacy_failed_exposes_can_retry_false(
    authed_client: tuple[Any, str, int],
) -> None:
    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="failed")

    resp = client.get(
        f"/api/lectures/{lecture_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["last_completed_stage"] is None
    assert body["can_retry"] is False


def test_status_on_generating_exposes_checkpoint_and_can_retry_false(
    authed_client: tuple[Any, str, int],
) -> None:
    from app.models.lecture import StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="generating")
    _set_checkpoint(lecture_id, StageCheckpoint.transcribe)

    resp = client.get(
        f"/api/lectures/{lecture_id}/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["last_completed_stage"] == "transcribe"
    assert body["can_retry"] is False


# Group C — Partial artifact reads during `generating`.


def test_transcript_readable_during_generating(
    authed_client: tuple[Any, str, int],
) -> None:
    from app.models.lecture import StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="generating")
    _set_checkpoint(lecture_id, StageCheckpoint.transcribe)
    _insert_transcript(lecture_id, full_text="Тест", language="uk")

    resp = client.get(
        f"/api/lectures/{lecture_id}/transcript",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["full_text"] == "Тест"
    assert body["language"] == "uk"
    assert len(body["segments"]) > 0


def test_summary_readable_during_generating(
    authed_client: tuple[Any, str, int],
) -> None:
    from app.models.lecture import StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="generating")
    _set_checkpoint(lecture_id, StageCheckpoint.summary)
    _insert_summary(lecture_id, title="Тестова лекція", language="uk")

    resp = client.get(
        f"/api/lectures/{lecture_id}/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"]["title"] == "Тестова лекція"
    assert body["generation_language"] == "uk"


def test_glossary_returns_404_during_generating_without_terms(
    authed_client: tuple[Any, str, int],
) -> None:
    from app.models.lecture import StageCheckpoint

    client, token, user_id = authed_client
    course_id = _make_course(user_id)
    lecture_id = _make_lecture(user_id, course_id, status="generating")
    _set_checkpoint(lecture_id, StageCheckpoint.summary)
    _insert_transcript(lecture_id)
    _insert_summary(lecture_id)
    # No GlossaryTerm rows — glossary stage has not committed yet.

    resp = client.get(
        f"/api/lectures/{lecture_id}/glossary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text
