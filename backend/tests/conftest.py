"""Test configuration: env setup before app.* imports, plus shared fixtures.

The session-scoped `_configure_test_environment` fixture runs at collection time so
`STORAGE_ROOT`, `DATABASE_URL`, and friends are set before any `from app...` import
executes at module top level in the rest of the test files. The `app.core.db` module
builds its engine and calls `_ensure_storage_dirs()` at import time using
`get_settings()`, so environment MUST be set (and the settings cache MUST be cleared)
before the first import.
"""
from __future__ import annotations

import io
import os
import secrets
import shutil
import sys
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Make `import app...` work when pytest is launched from the repo root
# (`pytest backend/tests`) without editable-installing the backend package.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ---------------------------------------------------------------------------
# Session-scoped environment setup — must run BEFORE any `app.*` import.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _configure_test_environment(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Path]:
    """Prepare env vars + clear the settings cache before any app import."""
    root = tmp_path_factory.mktemp("iscm-tests")
    storage = root / "storage"
    db_file = root / "test.db"

    os.environ["STORAGE_ROOT"] = str(storage)
    # SQLAlchemy expects sqlite:///<absolute-or-relative-path>; POSIX-style is safe on Windows.
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"
    # pydantic-settings 2.15 pre-parses env vars for complex fields as JSON before running
    # the `@field_validator(mode='before')` CSV splitter, so we hand it JSON directly.
    os.environ["CORS_ORIGINS"] = '["http://localhost:5173"]'
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["WHISPER_MODEL_SIZE"] = "tiny"
    os.environ["WHISPER_DEVICE"] = "cpu"
    os.environ["WHISPER_COMPUTE_TYPE"] = "int8"

    # Import order matters: env first, then invalidate the settings singleton so
    # every subsequent `get_settings()` picks up our test env.
    from app.core.config import get_settings

    get_settings.cache_clear()

    yield root


# ---------------------------------------------------------------------------
# Per-test isolation: fresh schema + drained job queue + FastAPI TestClient.
# ---------------------------------------------------------------------------


def _reset_job_lock_state() -> None:
    """Drain the queue and release the module-level lock if a prior test left it dirty."""
    from app.core import job_lock

    while not job_lock.job_queue.empty():
        try:
            job_lock.job_queue.get_nowait()
            job_lock.job_queue.task_done()
        except Exception:
            break
    try:
        if job_lock.is_locked():
            job_lock.release()
    except Exception:
        pass


def _reset_database() -> None:
    """Drop and recreate every table on the shared session-scoped SQLite file."""
    from app.core.db import Base, engine
    import app.models  # noqa: F401 — force model modules to register with Base.metadata

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _reset_storage_dir() -> None:
    """Wipe media leftovers so per-test user/lecture-id collisions can't cross-leak."""
    from app.core.config import get_settings

    settings = get_settings()
    shutil.rmtree(settings.media_root, ignore_errors=True)
    settings.media_root.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Fresh FastAPI `TestClient` with mocked worker + Ollama probe + empty DB."""
    # Patch worker lifecycle so the lifespan does not spawn a real background thread.
    from app.workers import worker as worker_module

    monkeypatch.setattr(worker_module, "start_worker", lambda: None)
    monkeypatch.setattr(worker_module, "stop_worker", lambda *a, **kw: None)

    # Silence the startup Ollama probe.
    from app.services import llm as llm_module

    monkeypatch.setattr(llm_module, "list_models", lambda: ["qwen2.5:7b-instruct"])

    _reset_job_lock_state()
    _reset_database()
    _reset_storage_dir()

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def authed_client(client: Any) -> tuple[Any, str, int]:
    """Register + login a fresh user; return `(client, token, user_id)`."""
    username = "alice_" + secrets.token_hex(4)
    password = "password123"

    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 201, resp.text
    user_id = int(resp.json()["id"])

    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]

    return client, token, user_id


@pytest.fixture
def second_user(client: Any) -> dict[str, Any]:
    """Register a second user without logging them in (login would evict user A)."""
    username = "bob_" + secrets.token_hex(4)
    password = "password456"

    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return {
        "username": username,
        "password": password,
        "id": int(resp.json()["id"]),
    }


# ---------------------------------------------------------------------------
# Deterministic audio fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def wav_bytes() -> bytes:
    """A 1-second silent 16 kHz mono 16-bit PCM WAV, built via the stdlib `wave` module."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)  # 16000 silent 16-bit samples
    return buf.getvalue()


@pytest.fixture
def mp3_bytes() -> bytes:
    """A byte-string that passes `sniff_magic` as MP3.

    The upload path only reads the first 12 bytes for the magic-byte check. We craft
    an ID3v2 header (`ID3\\x04\\x00\\x00` + 4-byte size) followed by fake MPEG frame
    headers so the resulting stream survives both `validate_mime('audio/mpeg')` and
    `sniff_magic()`. No real audio is required because `probe_duration` / `normalise`
    are patched by `fake_ffmpeg` and ASR is patched by `mock_asr`.
    """
    id3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"  # 10-byte minimal ID3v2 header
    fake_frames = b"\xff\xfb\x90\x00" * 128  # 512 bytes of "MPEG-1 Layer III" sync frames
    return id3 + fake_frames


# ---------------------------------------------------------------------------
# Patch fixtures — external process shims.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip real ffmpeg/ffprobe.

    - `probe_duration` returns a constant 10.0 seconds.
    - `normalise` becomes a plain `shutil.copy(src, dst)` so downstream ASR gets a file.
    """
    from app.services import media as media_svc

    def _fake_probe(_path: Path) -> float:
        return 10.0

    def _fake_normalise(path_in: Path, path_out: Path) -> None:
        path_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path_in, path_out)

    monkeypatch.setattr(media_svc, "probe_duration", _fake_probe)
    monkeypatch.setattr(media_svc, "normalise", _fake_normalise)


@pytest.fixture
def mock_asr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace `asr.transcribe` with a canned two-segment Ukrainian transcript."""
    from app.schemas.transcript import SegmentDraft
    from app.services import asr as asr_module

    def _fake_transcribe(_path: Path) -> tuple[list[SegmentDraft], str]:
        segments = [
            SegmentDraft(
                index=0,
                start_seconds=0.0,
                end_seconds=5.0,
                text="Перший тестовий сегмент лекції.",
                confidence=-0.2,
            ),
            SegmentDraft(
                index=1,
                start_seconds=5.0,
                end_seconds=10.0,
                text="Другий тестовий сегмент лекції.",
                confidence=-0.25,
            ),
        ]
        return segments, "uk"

    monkeypatch.setattr(asr_module, "transcribe", _fake_transcribe)


@pytest.fixture
def mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace `llm.generate_json` with a schema-aware canned reply.

    Chooses the returned document based on the `schema` kwarg so both summary and
    glossary generators are satisfied.
    """
    from app.schemas.glossary import GlossaryDocument, GlossaryEntry
    from app.schemas.summary import (
        SummaryDocument,
        SummaryHighlight,
        SummarySection,
    )
    from app.services import glossary_generator, llm as llm_module, summary_generator

    def _fake_generate_json(_prompt: str, *, schema: Any, model: str | None = None) -> Any:
        if schema is SummaryDocument:
            return SummaryDocument(
                title="Test lecture",
                language="uk",
                sections=[
                    SummarySection(
                        heading="Вступ",
                        timestamp_seconds=0.0,
                        bullets=["Ключова теза номер один", "Ключова теза номер два"],
                        highlights=[
                            SummaryHighlight(
                                kind="definition",
                                text="Тестове визначення.",
                                timestamp_seconds=1.0,
                            )
                        ],
                        subsections=[],
                    )
                ],
            )
        if schema is GlossaryDocument:
            entries = [
                GlossaryEntry(
                    term=f"Термін {i}",
                    definition=f"Визначення терміна номер {i}, згадане у лекції.",
                    first_mention_seconds=float(i),
                )
                for i in range(1, 11)
            ]
            return GlossaryDocument(language="uk", entries=entries)
        raise AssertionError(f"unexpected schema for mock_llm: {schema!r}")

    # Patch the source module for defensive coverage, and also rebind the names
    # already imported into the generator modules at module load time.
    monkeypatch.setattr(llm_module, "generate_json", _fake_generate_json)
    monkeypatch.setattr(summary_generator, "generate_json", _fake_generate_json)
    monkeypatch.setattr(glossary_generator, "generate_json", _fake_generate_json)


@pytest.fixture
def mock_worker_enqueue(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace `worker.enqueue` with a MagicMock so tests can assert call args."""
    from app.workers import worker as worker_module

    mock = MagicMock(return_value=None)
    monkeypatch.setattr(worker_module, "enqueue", mock)
    return mock


# ---------------------------------------------------------------------------
# One-shot font download for PDF tests.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def dejavu_font() -> Path | None:
    """Download DejaVuSans.ttf into the app assets folder once per test session.

    Returns the font path on success, or `None` if the download failed (in which
    case PDF tests should `pytest.skip('no network for font download')`).
    """
    from app.core.config import get_settings  # noqa: F401 — cache already populated

    font_path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "assets"
        / "fonts"
        / "DejaVuSans.ttf"
    )
    if font_path.exists() and font_path.stat().st_size > 0:
        return font_path

    url = (
        "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/"
        "version_2_37/ttf/DejaVuSans.ttf"
    )
    try:
        import httpx

        with httpx.Client(timeout=30.0, follow_redirects=True) as c:
            resp = c.get(url)
            resp.raise_for_status()
            font_path.parent.mkdir(parents=True, exist_ok=True)
            font_path.write_bytes(resp.content)
    except Exception:
        return None
    return font_path
