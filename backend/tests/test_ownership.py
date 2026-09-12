"""Cross-user isolation: every user-scoped read/write must 404 for non-owners."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


def _make_user(username: str, password: str = "password123") -> tuple[str, int]:
    """Insert a user directly and return `(username, id)` for direct DB linkage."""
    from app.core.db import SessionLocal
    from app.core.security import hash_password
    from app.models.user import User

    db = SessionLocal()
    try:
        u = User(username=username, password_hash=hash_password(password))
        db.add(u)
        db.commit()
        db.refresh(u)
        return username, u.id
    finally:
        db.close()


def _login(client: Any, username: str, password: str = "password123") -> str:
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _pre_populate_user_a_lecture(user_id: int) -> tuple[int, int]:
    """Insert course + lecture + transcript + summary + glossary rows for user A.

    Returns `(course_id, lecture_id)`.
    """
    from app.core.db import SessionLocal
    from app.models.course import Course
    from app.models.glossary import GlossaryTerm
    from app.models.lecture import Lecture, LectureStatus
    from app.models.summary import Summary
    from app.models.transcript import Transcript, TranscriptSegment
    from app.schemas.summary import SummaryDocument, SummarySection

    db = SessionLocal()
    try:
        course = Course(user_id=user_id, title="Owned course")
        db.add(course)
        db.flush()

        lecture = Lecture(
            course_id=course.id,
            user_id=user_id,
            title="Owned lecture",
            original_filename="owned.wav",
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

        summary_doc = SummaryDocument(
            title="Owned lecture",
            language="uk",
            sections=[
                SummarySection(
                    heading="Intro",
                    timestamp_seconds=0.0,
                    bullets=["Point A"],
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
                    term=f"Term {i}",
                    definition=f"Definition {i}.",
                    first_mention_seconds=float(i),
                    prompt_version="glossary_v1",
                    generation_language="uk",
                )
            )
        db.commit()
        return course.id, lecture.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Shared setup fixture.
# ---------------------------------------------------------------------------


@pytest.fixture
def two_users_and_lecture(client: Any) -> dict[str, Any]:
    """User A (owner) with a full lecture + course; user B (intruder) logged in."""
    name_a = "alice_" + secrets.token_hex(4)
    name_b = "bob_" + secrets.token_hex(4)
    _, user_a_id = _make_user(name_a)
    _, user_b_id = _make_user(name_b)

    course_id, lecture_id = _pre_populate_user_a_lecture(user_a_id)

    # Log user B in last so A's session (never created here anyway) doesn't survive.
    token_b = _login(client, name_b)
    # Log user A in AFTER — this evicts B.  Actually we WANT B active for the intruder tests.
    # Log A back after doing the intruder checks in tests where the owner needs auth.
    return {
        "client": client,
        "user_a_id": user_a_id,
        "user_a_name": name_a,
        "user_b_id": user_b_id,
        "user_b_name": name_b,
        "token_b": token_b,
        "course_id": course_id,
        "lecture_id": lecture_id,
    }


# ---------------------------------------------------------------------------
# Intruder access checks.
# ---------------------------------------------------------------------------


def test_user_b_cannot_see_user_a_course(two_users_and_lecture: dict[str, Any]) -> None:
    ctx = two_users_and_lecture
    resp = ctx["client"].get(
        f"/api/courses/{ctx['course_id']}",
        headers={"Authorization": f"Bearer {ctx['token_b']}"},
    )
    assert resp.status_code == 404


def test_user_b_cannot_see_user_a_lecture(two_users_and_lecture: dict[str, Any]) -> None:
    ctx = two_users_and_lecture
    resp = ctx["client"].get(
        f"/api/lectures/{ctx['lecture_id']}",
        headers={"Authorization": f"Bearer {ctx['token_b']}"},
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "path_suffix",
    ["/transcript", "/summary", "/glossary", "/status", "/audio"],
)
def test_user_b_cannot_read_transcript_summary_glossary_status_audio(
    two_users_and_lecture: dict[str, Any], path_suffix: str
) -> None:
    ctx = two_users_and_lecture
    url = f"/api/lectures/{ctx['lecture_id']}{path_suffix}"
    resp = ctx["client"].get(url, headers={"Authorization": f"Bearer {ctx['token_b']}"})
    assert resp.status_code == 404, f"{path_suffix}: {resp.status_code} — {resp.text}"


@pytest.mark.parametrize("fmt", ["txt", "pdf"])
def test_user_b_cannot_export_txt_or_pdf(
    two_users_and_lecture: dict[str, Any], fmt: str
) -> None:
    ctx = two_users_and_lecture
    resp = ctx["client"].get(
        f"/api/lectures/{ctx['lecture_id']}/export/{fmt}",
        headers={"Authorization": f"Bearer {ctx['token_b']}"},
    )
    # 404 must happen before we hit the (possibly missing) font code path.
    assert resp.status_code == 404, resp.text


def test_user_b_cannot_delete_user_a_course_or_lecture(
    two_users_and_lecture: dict[str, Any],
) -> None:
    ctx = two_users_and_lecture
    headers = {"Authorization": f"Bearer {ctx['token_b']}"}
    r1 = ctx["client"].delete(f"/api/courses/{ctx['course_id']}", headers=headers)
    assert r1.status_code == 404
    r2 = ctx["client"].delete(f"/api/lectures/{ctx['lecture_id']}", headers=headers)
    assert r2.status_code == 404


def test_owner_can_see_everything(two_users_and_lecture: dict[str, Any]) -> None:
    """Sanity: A's own resources ARE accessible once A logs in (evicting B)."""
    ctx = two_users_and_lecture
    token_a = _login(ctx["client"], ctx["user_a_name"])
    headers = {"Authorization": f"Bearer {token_a}"}

    r_course = ctx["client"].get(f"/api/courses/{ctx['course_id']}", headers=headers)
    assert r_course.status_code == 200

    r_lecture = ctx["client"].get(f"/api/lectures/{ctx['lecture_id']}", headers=headers)
    assert r_lecture.status_code == 200

    r_transcript = ctx["client"].get(
        f"/api/lectures/{ctx['lecture_id']}/transcript", headers=headers
    )
    assert r_transcript.status_code == 200

    r_summary = ctx["client"].get(
        f"/api/lectures/{ctx['lecture_id']}/summary", headers=headers
    )
    assert r_summary.status_code == 200

    r_glossary = ctx["client"].get(
        f"/api/lectures/{ctx['lecture_id']}/glossary", headers=headers
    )
    assert r_glossary.status_code == 200
    assert len(r_glossary.json()["entries"]) == 10
