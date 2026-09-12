"""faster-whisper wrapper: lazy singleton model, VAD-enabled transcribe, fallback language."""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.config import get_settings
from app.schemas.transcript import SegmentDraft

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class AsrError(RuntimeError):
    """Raised when faster-whisper fails to transcribe an audio file."""


_model: WhisperModel | None = None
_model_lock = threading.Lock()


def _get_model() -> WhisperModel:
    """Return the process-wide `WhisperModel`, constructing it on first call."""
    global _model
    with _model_lock:
        if _model is None:
            settings = get_settings()
            # Import inside the guard so importing this module does not pull in ctranslate2.
            from faster_whisper import WhisperModel

            logger.info(
                "Loading faster-whisper model",
                extra={
                    "model_size": settings.WHISPER_MODEL_SIZE,
                    "device": settings.WHISPER_DEVICE,
                    "compute_type": settings.WHISPER_COMPUTE_TYPE,
                },
            )
            _model = WhisperModel(
                model_size_or_path=settings.WHISPER_MODEL_SIZE,
                device=settings.WHISPER_DEVICE,
                compute_type=settings.WHISPER_COMPUTE_TYPE,
            )
        return _model


def _run_whisper(path: Path, language: str | None) -> tuple[list[Any], Any]:
    """Invoke the model once. Returns `(segments_list, info)`, materialising the generator."""
    settings = get_settings()
    model = _get_model()
    segments_iter, info = model.transcribe(
        str(path),
        vad_filter=settings.WHISPER_VAD,
        language=language,
        beam_size=5,
    )
    return list(segments_iter), info


def transcribe(path: Path) -> tuple[list[SegmentDraft], str]:
    """Transcribe `path` and return `(segments, detected_language)`.

    Auto-detects the language; if the detection confidence is below
    `settings.WHISPER_LANGUAGE_DETECT_MIN_PROB` we log a warning and re-run once with
    `settings.WHISPER_FALLBACK_LANGUAGE` forced.
    """
    settings = get_settings()
    try:
        raw_segments, info = _run_whisper(path, language=None)
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
            raw_segments, info = _run_whisper(path, language=settings.WHISPER_FALLBACK_LANGUAGE)
            detected = settings.WHISPER_FALLBACK_LANGUAGE
    except Exception as exc:
        logger.exception("faster-whisper transcription failed for %s", path)
        raise AsrError(f"Transcription failed: {exc}") from exc

    drafts: list[SegmentDraft] = []
    for i, seg in enumerate(raw_segments):
        text = (getattr(seg, "text", "") or "").strip()
        # `avg_logprob` is a log-probability (typically in [-1, 0]); it is NOT a 0-1 confidence.
        # We persist it as-is; the UI is responsible for any presentation conversion.
        avg_logprob = getattr(seg, "avg_logprob", None)
        drafts.append(
            SegmentDraft(
                index=i,
                start_seconds=float(getattr(seg, "start", 0.0) or 0.0),
                end_seconds=float(getattr(seg, "end", 0.0) or 0.0),
                text=text,
                confidence=float(avg_logprob) if avg_logprob is not None else None,
            )
        )
    return drafts, detected


_WHITESPACE_RE = re.compile(r"\s+")


def join_full_text(segments: list[SegmentDraft]) -> str:
    """Concatenate segment texts with normalised single-space whitespace."""
    joined = " ".join(seg.text for seg in segments if seg.text)
    return _WHITESPACE_RE.sub(" ", joined).strip()
