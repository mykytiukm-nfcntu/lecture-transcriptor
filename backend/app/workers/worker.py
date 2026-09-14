"""Long-lived background worker thread that owns the job lock around each pipeline call."""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import UTC, datetime

from sqlalchemy.orm import Session as DBSession

from app.core import job_lock
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.job_lock import LockedError, job_queue
from app.models.glossary import GlossaryTerm
from app.models.lecture import Lecture, LectureStatus, StageCheckpoint
from app.models.summary import Summary
from app.models.transcript import Transcript
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
        lecture.finished_at = datetime.now(UTC)
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

ORPHAN_RESTART_MESSAGE = (
    "Server restarted while processing; retry to continue from the last completed stage."
)


def _derive_orphan_checkpoint(db: DBSession, lecture: Lecture) -> StageCheckpoint | None:
    """Return the highest stage whose artifact evidence exists for this lecture.

    Probes are cheap SELECT-1 style existence checks scoped by ``lecture_id`` plus a
    single ``normalized.wav`` disk stat under the canonical media path. Returns
    ``None`` when no artifact evidence exists (legacy grandfathered row).
    """
    lid = lecture.id
    if db.query(GlossaryTerm.id).filter(GlossaryTerm.lecture_id == lid).first() is not None:
        return StageCheckpoint.glossary
    if db.query(Summary.id).filter(Summary.lecture_id == lid).first() is not None:
        return StageCheckpoint.summary
    if db.query(Transcript.id).filter(Transcript.lecture_id == lid).first() is not None:
        return StageCheckpoint.transcribe
    normalized = get_settings().media_root / str(lecture.user_id) / str(lid) / "normalized.wav"
    if normalized.exists():
        return StageCheckpoint.normalize
    return None


def reconcile_orphaned_lectures() -> int:
    """Reconcile mid-flight lectures on startup, preserving partial-artifact checkpoints.

    Each non-terminal row's highest-matching stage checkpoint is derived from disk +
    DB evidence, then the row is flipped to ``failed`` with a "server restarted"
    message so the user can retry from the last committed stage. If a row already has
    committed glossary rows (the pipeline should have flipped it to ``completed``
    itself; defensive branch) it is flipped to ``completed`` instead. Returns the
    total number of rows reconciled. Idempotent: rows already in a terminal status
    are ignored, so repeat invocations are no-ops.
    """
    db: DBSession = SessionLocal()
    try:
        stuck = db.query(Lecture).filter(Lecture.status.in_(_ORPHANED_STATUSES)).all()
        if not stuck:
            return 0
        now = datetime.now(UTC)
        failed_count = 0
        completed_count = 0
        for lecture in stuck:
            checkpoint = _derive_orphan_checkpoint(db, lecture)
            if checkpoint is StageCheckpoint.glossary:
                logger.warning(
                    "Orphaned lecture %d already has glossary rows; reconciling to completed",
                    lecture.id,
                )
                lecture.status = LectureStatus.completed
                lecture.last_completed_stage = StageCheckpoint.glossary
                lecture.finished_at = now
                # Clear any stale error left by a prior failure attempt so a completed
                # lecture never surfaces an error string in the UI.
                lecture.error_message = None
                completed_count += 1
            else:
                lecture.status = LectureStatus.failed
                lecture.error_message = ORPHAN_RESTART_MESSAGE
                lecture.finished_at = now
                lecture.last_completed_stage = checkpoint
                failed_count += 1
        db.commit()
        if completed_count:
            logger.info(
                "Reconciled %d orphaned lecture(s) to failed, %d to completed",
                failed_count,
                completed_count,
            )
        else:
            logger.info("Reconciled %d orphaned lecture(s) to failed", failed_count)
        return failed_count + completed_count
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
                raw_id, generation_language, model = item
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
                pipeline.process_lecture(
                    lecture_id,
                    generation_language=generation_language,
                    model=model,
                )
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


def enqueue(
    lecture_id: int,
    generation_language: str | None = None,
    model: str | None = None,
) -> None:
    """Add `lecture_id` to the job queue. Raises `LockedError` if a job is already active.

    `generation_language` overrides the ASR-detected language when calling the LLM
    generators. `model` overrides `settings.OLLAMA_MODEL` for those same calls. Both `None`
    fall back to the process-wide defaults.
    """
    if job_lock.is_locked() or not job_queue.empty():
        raise LockedError("Another transcription is currently running")
    try:
        job_queue.put_nowait((lecture_id, generation_language, model))
    except queue.Full as exc:
        # Race between the check above and the put; treat as if the lock is held.
        raise LockedError("Another transcription is currently running") from exc
