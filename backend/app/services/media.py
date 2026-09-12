"""Media validation, ffprobe duration, ffmpeg normalisation, upload streaming."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import IO

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class MediaError(Exception):
    """Base class for all media-processing failures. Caught wholesale by the pipeline."""


class UnsupportedMediaTypeError(MediaError):
    """Uploaded file failed MIME or magic-byte validation."""


class MediaProbeError(MediaError):
    """`ffprobe` failed to report the media duration."""


class MediaNormaliseError(MediaError):
    """`ffmpeg` failed to normalise the media file."""


_ACCEPTED_MIMES: frozenset[str] = frozenset(
    {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/wave"}
)


def validate_mime(content_type: str | None) -> None:
    """Raise `UnsupportedMediaTypeError` if the declared MIME is not mp3/wav."""
    if not content_type:
        raise UnsupportedMediaTypeError("Missing Content-Type on upload")
    # Strip parameters like `;charset=...`, and normalise case.
    primary = content_type.split(";", 1)[0].strip().lower()
    if primary not in _ACCEPTED_MIMES:
        raise UnsupportedMediaTypeError(f"Unsupported Content-Type: {content_type!r}")


def sniff_magic(head: bytes) -> str | None:
    """Return `"mp3"`, `"wav"`, or `None` based on the first 12 bytes of the file."""
    if len(head) < 4:
        return None
    if head[:3] == b"ID3":
        return "mp3"
    # MPEG-1/2/2.5 Layer III frame sync: byte 0 = 0xFF, byte 1 has top 3 sync bits + any
    # MPEG version + Layer III (bits 1-2 = 01). Mask 0xE6 isolates those fields; 0xE2 is the match.
    if head[0] == 0xFF and (head[1] & 0xE6) == 0xE2:
        return "mp3"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    return None


def save_upload(user_id: int, lecture_id: int, filename: str, src_stream: IO[bytes]) -> Path:
    """Stream an upload to `storage/media/{user_id}/{lecture_id}/original.<ext>`.

    The on-disk extension is derived from the sniffed magic bytes, NEVER from the client's
    filename. Raises `UnsupportedMediaTypeError` if the bytes do not match mp3 or wav.
    """
    head = src_stream.read(12)
    ext = sniff_magic(head)
    if ext is None:
        logger.warning("Rejected upload %r: magic bytes do not match mp3/wav", filename)
        raise UnsupportedMediaTypeError(f"File contents are not a valid MP3 or WAV: {filename!r}")

    settings = get_settings()
    dest_dir = settings.media_root / str(user_id) / str(lecture_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"original.{ext}"

    with dest.open("wb") as out:
        out.write(head)
        while True:
            chunk = src_stream.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)

    return dest.resolve()


def probe_duration(path: Path) -> float:
    """Return the media duration in seconds via `ffprobe`. Raise `MediaProbeError` on failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise MediaProbeError("ffprobe not found on PATH") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error("ffprobe failed for %s: %s", path, stderr)
        raise MediaProbeError(f"ffprobe failed: {stderr}")

    try:
        payload = json.loads(result.stdout)
        return float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.error("ffprobe produced unparseable output for %s: %s", path, result.stdout)
        raise MediaProbeError(f"ffprobe returned unparseable output: {exc}") from exc


def normalise(path_in: Path, path_out: Path) -> None:
    """Downmix to mono, resample to 16 kHz, apply loudness normalisation, write to `path_out`."""
    path_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(path_in),
        "-ac", "1",
        "-ar", "16000",
        "-af", "loudnorm",
        str(path_out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise MediaNormaliseError("ffmpeg not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        logger.error("ffmpeg normalise failed for %s: %s", path_in, stderr)
        raise MediaNormaliseError(f"ffmpeg normalise failed: {stderr}") from exc


def estimated_processing_seconds(duration_seconds: float) -> float:
    """Rough preliminary ETA shown to the user before the job starts.

    Heuristic: transcription cost dominates and is roughly linear in audio duration on CPU;
    the +30 s constant accounts for LLM summary/glossary passes on a short lecture. This is
    intentionally coarse - the user sees an elapsed timer once the job actually starts.
    """
    return duration_seconds * 1.2 + 30.0
