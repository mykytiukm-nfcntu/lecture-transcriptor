"""Module-level primitives enforcing the single-active-transcription-job invariant."""
from __future__ import annotations

import queue
import threading
from typing import Any


class LockedError(RuntimeError):
    """Raised by the API layer when a job cannot start because one is already active."""


_lock = threading.Lock()

# The worker thread (wave 3) consumes from this queue. Bound to size 1 so the API
# layer never queues a second job behind the running one — 409 is returned instead.
job_queue: queue.Queue[Any] = queue.Queue(maxsize=1)


def try_acquire() -> bool:
    """Non-blocking acquire. Returns True iff the caller now holds the lock."""
    return _lock.acquire(blocking=False)


def release() -> None:
    """Release the lock; called by the worker when a job finishes (success or failure)."""
    _lock.release()


def is_locked() -> bool:
    """True while a job is running."""
    return _lock.locked()
