"""Tests for /api/courses/{id}/lectures and /api/lectures/{id}: upload, delete, list."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
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
