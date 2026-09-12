"""In-memory intra-stage progress tracker for the pipeline (ephemeral, per-lecture)."""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressSnapshot:
    """Immutable snapshot returned to consumers so they cannot mutate the store."""

    percent: float
    stage: str | None = None


_lock = threading.Lock()
_progress: dict[int, ProgressSnapshot] = {}


def set_progress(lecture_id: int, percent: float) -> None:
    """Update `percent` for `lecture_id`, clamped to [0, 100]. Keeps whatever `stage` is set."""
    clamped = max(0.0, min(100.0, percent))
    with _lock:
        prev = _progress.get(lecture_id)
        stage = prev.stage if prev is not None else None
        _progress[lecture_id] = ProgressSnapshot(percent=clamped, stage=stage)


def set_stage(lecture_id: int, stage: str) -> None:
    """Set the current sub-stage label and reset `percent` to 0 for the new stage."""
    with _lock:
        _progress[lecture_id] = ProgressSnapshot(percent=0.0, stage=stage)


def get_progress(lecture_id: int) -> ProgressSnapshot | None:
    """Return the current snapshot for `lecture_id`, or None if none recorded."""
    with _lock:
        return _progress.get(lecture_id)


def clear_progress(lecture_id: int) -> None:
    """Drop the progress entry for `lecture_id`. Called when the pipeline exits."""
    with _lock:
        _progress.pop(lecture_id, None)
