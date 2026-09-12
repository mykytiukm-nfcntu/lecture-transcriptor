"""faster-whisper wrapper: lazy singleton model, VAD-enabled transcribe, fallback language."""
from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.config import get_settings
from app.schemas.transcript import SegmentDraft

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, float], None]


class AsrError(RuntimeError):
    """Raised when faster-whisper fails to transcribe an audio file."""


_model: WhisperModel | None = None
_model_lock = threading.Lock()


def _resolve_cpu_threads(configured: int) -> int:
    """Return `configured` when positive; otherwise the logical CPU count (falling back to 4)."""
    if configured > 0:
        return configured
    return os.cpu_count() or 4


def _get_model() -> WhisperModel:
    """Return the process-wide `WhisperModel`, constructing it on first call."""
    global _model
    with _model_lock:
        if _model is None:
            settings = get_settings()
            # Import inside the guard so importing this module does not pull in ctranslate2.
            from faster_whisper import WhisperModel

            cpu_threads = _resolve_cpu_threads(settings.WHISPER_CPU_THREADS)
            logger.info(
                "Loading faster-whisper model",
                extra={
                    "model_size": settings.WHISPER_MODEL_SIZE,
                    "device": settings.WHISPER_DEVICE,
                    "compute_type": settings.WHISPER_COMPUTE_TYPE,
                    "cpu_threads": cpu_threads,
                },
            )
            _model = WhisperModel(
                model_size_or_path=settings.WHISPER_MODEL_SIZE,
                device=settings.WHISPER_DEVICE,
                compute_type=settings.WHISPER_COMPUTE_TYPE,
                cpu_threads=cpu_threads,
            )
        return _model


def _start_whisper(path: Path, language: str | None) -> tuple[Any, Any]:
    """Kick off the model. Returns `(segments_iter, info)` without draining the iterator."""
    settings = get_settings()
    model = _get_model()
    segments_iter, info = model.transcribe(
        str(path),
        vad_filter=settings.WHISPER_VAD,
        language=language,
        beam_size=5,
    )
    return segments_iter, info


def _drain(
    segments_iter: Any, total_duration: float, on_progress: ProgressCallback | None
) -> list[SegmentDraft]:
    """Iterate the faster-whisper generator, publishing progress after each segment."""
    drafts: list[SegmentDraft] = []
    for i, seg in enumerate(segments_iter):
        text = (getattr(seg, "text", "") or "").strip()
        # `avg_logprob` is a log-probability (typically in [-1, 0]); it is NOT a 0-1 confidence.
        # We persist it as-is; the UI is responsible for any presentation conversion.
        avg_logprob = getattr(seg, "avg_logprob", None)
        end_seconds = float(getattr(seg, "end", 0.0) or 0.0)
        drafts.append(
            SegmentDraft(
                index=i,
                start_seconds=float(getattr(seg, "start", 0.0) or 0.0),
                end_seconds=end_seconds,
                text=text,
                confidence=float(avg_logprob) if avg_logprob is not None else None,
            )
        )
        if on_progress is not None and total_duration > 0.0:
            try:
                on_progress(end_seconds, total_duration)
            except Exception:  # noqa: BLE001 - progress reporting must never break ASR.
                logger.exception("Progress callback raised; continuing transcription")
    return drafts


def transcribe(
    path: Path, *, on_progress: ProgressCallback | None = None
) -> tuple[list[SegmentDraft], str]:
    """Transcribe `path` and return `(segments, detected_language)`.

    Auto-detects the language; if the detection confidence is below
    `settings.WHISPER_LANGUAGE_DETECT_MIN_PROB` we log a warning and re-run once with
    `settings.WHISPER_FALLBACK_LANGUAGE` forced.

    When `on_progress` is provided it is called after each yielded segment with
    `(seconds_transcribed, total_audio_seconds)` so callers can publish intra-stage
    progress.
    """
    settings = get_settings()
    try:
        segments_iter, info = _start_whisper(path, language=None)
        detected = info.language or settings.WHISPER_FALLBACK_LANGUAGE
        probability = float(info.language_probability or 0.0)

        if probability < settings.WHISPER_LANGUAGE_DETECT_MIN_PROB:
            logger.warning(
                "Language detection below threshold; falling back",
                extra={
                    "detected": detected,
                    "probability": probability,
                    "fallback": settings.WHISPER_FALLBACK_LANGUAGE,
                },
            )
            segments_iter, info = _start_whisper(
                path, language=settings.WHISPER_FALLBACK_LANGUAGE
            )
            detected = settings.WHISPER_FALLBACK_LANGUAGE

        total_duration = float(getattr(info, "duration", 0.0) or 0.0)
        drafts = _drain(segments_iter, total_duration, on_progress)
    except Exception as exc:
        logger.exception("faster-whisper transcription failed for %s", path)
        raise AsrError(f"Transcription failed: {exc}") from exc

    return drafts, detected


_WHITESPACE_RE = re.compile(r"\s+")


def join_full_text(segments: list[SegmentDraft]) -> str:
    """Concatenate segment texts with normalised single-space whitespace."""
    joined = " ".join(seg.text for seg in segments if seg.text)
    return _WHITESPACE_RE.sub(" ", joined).strip()

