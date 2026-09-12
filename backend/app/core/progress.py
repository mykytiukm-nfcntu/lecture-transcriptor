"""In-memory intra-stage progress tracker for the pipeline (ephemeral, per-lecture)."""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressSnapshot:
    """Immutable snapshot returned to consumers so they cannot mutate the store."""

    percent: float


_lock = threading.Lock()
_progress: dict[int, ProgressSnapshot] = {}


def set_progress(lecture_id: int, percent: float) -> None:
    """Record the intra-stage progress for `lecture_id`, clamped to [0, 100]."""
    clamped = max(0.0, min(100.0, percent))
    with _lock:
        _progress[lecture_id] = ProgressSnapshot(percent=clamped)


def get_progress(lecture_id: int) -> ProgressSnapshot | None:
    """Return the current snapshot for `lecture_id`, or None if none recorded."""
    with _lock:
        return _progress.get(lecture_id)


def clear_progress(lecture_id: int) -> None:
    """Drop the progress entry for `lecture_id`. Called when the pipeline exits."""
    with _lock:
        _progress.pop(lecture_id, None)
