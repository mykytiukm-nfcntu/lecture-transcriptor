"""Long-lived background worker thread that owns the job lock around each pipeline call."""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from app.core import job_lock
from app.core.db import SessionLocal
from app.core.job_lock import LockedError, job_queue
from app.models.lecture import Lecture, LectureStatus
from app.services import pipeline

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None
_lifecycle_lock = threading.Lock()


def _mark_failed_without_lock(lecture_id: int, message: str) -> None:
    """Flip a lecture to `failed` in its own DB session. Used when the pipeline never ran."""
    db: DBSession = SessionLocal()
    try:
        lecture = db.get(Lecture, lecture_id)
        if lecture is None:
            return
        lecture.status = LectureStatus.failed
        lecture.error_message = message[:2000]
        lecture.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to mark lecture %d as failed", lecture_id)
    finally:
        db.close()


_ORPHANED_STATUSES: tuple[LectureStatus, ...] = (
    LectureStatus.queued,
    LectureStatus.normalizing,
    LectureStatus.transcribing,
    LectureStatus.generating,
)


def reconcile_orphaned_lectures() -> int:
    """Mark every non-terminal lecture as failed on startup (pipeline is non-resumable)."""
    db: DBSession = SessionLocal()
    try:
        stuck = db.query(Lecture).filter(Lecture.status.in_(_ORPHANED_STATUSES)).all()
        if not stuck:
            return 0
        now = datetime.now(timezone.utc)
        for lecture in stuck:
            lecture.status = LectureStatus.failed
            lecture.error_message = "Server restarted while processing; please re-upload."
            lecture.finished_at = now
        db.commit()
        logger.info("Reconciled %d orphaned lecture(s) to failed", len(stuck))
        return len(stuck)
    except Exception:
        db.rollback()
        logger.exception("Failed to reconcile orphaned lectures")
        return 0
    finally:
        db.close()


def _worker_loop() -> None:
    logger.info("Worker thread started")
    while True:
        item = job_queue.get()
        try:
            if item is None:
                logger.info("Worker received shutdown sentinel; exiting")
                return
            try:
                raw_id, generation_language = item
                lecture_id = int(raw_id)
            except (TypeError, ValueError):
                logger.error("Worker received malformed queue item: %r", item)
                continue

            if not job_lock.try_acquire():
                # Defensive: the API is supposed to guarantee this never happens.
                logger.error(
                    "Worker could not acquire job lock for lecture %d; marking failed",
                    lecture_id,
                )
                _mark_failed_without_lock(lecture_id, "Concurrent job detected in worker")
                continue

            try:
                pipeline.process_lecture(lecture_id, generation_language=generation_language)
            except Exception:
                logger.exception("Uncaught pipeline exception for lecture %d", lecture_id)
                _mark_failed_without_lock(lecture_id, "Uncaught worker exception")
            finally:
                job_lock.release()
        finally:
            job_queue.task_done()


def start_worker() -> None:
    """Spawn the daemon worker thread if it is not already running. Idempotent."""
    global _worker_thread
    with _lifecycle_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        thread = threading.Thread(target=_worker_loop, name="lecture-worker", daemon=True)
        thread.start()
        _worker_thread = thread
        logger.info("Worker thread spawned")


def stop_worker(timeout: float = 10.0) -> None:
    """Signal shutdown, post the sentinel, and join the worker. Idempotent."""
    global _worker_thread
    with _lifecycle_lock:
        thread = _worker_thread
        if thread is None or not thread.is_alive():
            _worker_thread = None
            return

        # The queue holds at most one item. If it's currently full (a job is pending), wait
        # briefly for the worker to consume it, then post the sentinel.
        deadline = time.monotonic() + timeout
        posted = False
        while not posted:
            try:
                job_queue.put(None, timeout=0.1)
                posted = True
            except queue.Full:
                if time.monotonic() >= deadline:
                    logger.warning("stop_worker: could not post shutdown sentinel in time")
                    break

        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning("stop_worker: worker did not exit within %.1fs", timeout)
        _worker_thread = None


def enqueue(lecture_id: int, generation_language: str | None = None) -> None:
    """Add `lecture_id` to the job queue. Raises `LockedError` if a job is already active.

    `generation_language` is an optional per-request override that flows through to the
    pipeline in place of the ASR-detected language when calling the summary/glossary
    generators. `None` means "use the detected language".
    """
    if job_lock.is_locked() or not job_queue.empty():
        raise LockedError("Another transcription is currently running")
    try:
        job_queue.put_nowait((lecture_id, generation_language))
    except queue.Full as exc:
        # Race between the check above and the put; treat as if the lock is held.
        raise LockedError("Another transcription is currently running") from exc
