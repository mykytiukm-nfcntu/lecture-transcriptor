"""Per-lecture orchestration: media -> ASR -> chunking -> LLM -> persistence."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import correlation_id
from app.core.progress import clear_progress, set_progress
from app.models.glossary import GlossaryTerm
from app.models.lecture import Lecture, LectureStatus
from app.models.summary import Summary
from app.models.transcript import Transcript, TranscriptSegment
from app.services import asr, chunking, glossary_generator, llm, media, summary_generator

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mark_failed(db: DBSession, lecture: Lecture, message: str) -> None:
    logger.error("Pipeline failed: %s", message, extra={"lecture_id": lecture.id})
    lecture.status = LectureStatus.failed
    lecture.error_message = message[:2000]
    lecture.finished_at = _utcnow()
    db.commit()


def _find_original(user_id: int, lecture_id: int) -> Path | None:
    settings = get_settings()
    media_dir = settings.media_root / str(user_id) / str(lecture_id)
    if not media_dir.exists():
        return None
    for candidate in media_dir.glob("original.*"):
        return candidate
    return None


def process_lecture(lecture_id: int, generation_language: str | None = None) -> None:
    """Run the full pipeline for one lecture. Never raises - failure is stored on the row.

    `generation_language`, if provided, replaces the ASR-detected language when calling
    the summary and glossary generators and is what gets persisted on those artifact
    rows. `lecture.language` always stores the audio language detected by ASR.
    """
    correlation_id.set(f"lecture-{lecture_id}")
    logger.info("Pipeline start", extra={"lecture_id": lecture_id})

    db: DBSession = SessionLocal()
    try:
        lecture = db.get(Lecture, lecture_id)
        if lecture is None:
            logger.error("Lecture %d not found; aborting pipeline", lecture_id)
            return

        lecture.started_at = _utcnow()
        lecture.error_message = None
        lecture.status = LectureStatus.normalizing
        db.commit()
        set_progress(lecture_id, 0.0)

        original_path = _find_original(lecture.user_id, lecture.id)
        if original_path is None:
            _mark_failed(db, lecture, "Original media file not found on disk")
            return

        normalized_path = original_path.parent / "normalized.wav"

        def _on_norm_progress(current: float, total: float) -> None:
            set_progress(lecture_id, (current / total) * 100.0)

        try:
            media.normalise(
                original_path,
                normalized_path,
                total_seconds=lecture.duration_seconds,
                on_progress=_on_norm_progress,
            )
        except media.MediaError as exc:
            _mark_failed(db, lecture, f"Normalisation failed: {exc}")
            return

        lecture.status = LectureStatus.transcribing
        db.commit()
        set_progress(lecture_id, 0.0)

        def _on_asr_progress(current: float, total: float) -> None:
            set_progress(lecture_id, (current / total) * 100.0)

        try:
            segment_drafts, detected_language = asr.transcribe(
                normalized_path, on_progress=_on_asr_progress
            )
        except asr.AsrError as exc:
            _mark_failed(db, lecture, f"Transcription failed: {exc}")
            return
        except Exception as exc:
            _mark_failed(db, lecture, f"Transcription failed: {exc}")
            return

        full_text = asr.join_full_text(segment_drafts)
        transcript = Transcript(
            lecture_id=lecture.id,
            full_text=full_text,
            language=detected_language,
        )
        db.add(transcript)
        db.flush()
        for draft in segment_drafts:
            db.add(
                TranscriptSegment(
                    transcript_id=transcript.id,
                    index=draft.index,
                    start_seconds=draft.start_seconds,
                    end_seconds=draft.end_seconds,
                    text=draft.text,
                    confidence=draft.confidence,
                )
            )
        lecture.language = detected_language
        db.commit()

        lecture.status = LectureStatus.generating
        db.commit()
        set_progress(lecture_id, 0.0)

        settings = get_settings()
        chunks = chunking.chunk_segments(
            segment_drafts,
            target_tokens=settings.CHUNK_TOKENS,
            overlap_tokens=settings.CHUNK_OVERLAP_TOKENS,
        )

        resolved_language = generation_language or detected_language

        gen_total = summary_generator.step_count(chunks) + glossary_generator.STEP_COUNT
        gen_done = 0

        def _on_gen_step() -> None:
            nonlocal gen_done
            gen_done += 1
            if gen_total > 0:
                set_progress(lecture_id, (gen_done / gen_total) * 100.0)

        try:
            summary_doc = summary_generator.generate(
                chunks,
                segment_drafts,
                language=resolved_language,
                lecture_title=lecture.title,
                on_step=_on_gen_step,
            )
        except llm.LlmGenerationError as exc:
            _mark_failed(db, lecture, f"Summary generation failed: {exc}")
            return

        try:
            glossary_doc = glossary_generator.generate(
                chunks,
                segment_drafts,
                language=resolved_language,
                lecture_title=lecture.title,
                on_step=_on_gen_step,
            )
        except llm.LlmGenerationError as exc:
            _mark_failed(db, lecture, f"Glossary generation failed: {exc}")
            return
        set_progress(lecture_id, 100.0)

        db.add(
            Summary(
                lecture_id=lecture.id,
                content_json=summary_doc.model_dump_json(),
                prompt_version=summary_generator.PROMPT_VERSION,
                generation_language=resolved_language,
            )
        )
        for entry in glossary_doc.entries:
            db.add(
                GlossaryTerm(
                    lecture_id=lecture.id,
                    term=entry.term,
                    definition=entry.definition,
                    first_mention_seconds=entry.first_mention_seconds,
                    prompt_version=glossary_generator.PROMPT_VERSION,
                    generation_language=resolved_language,
                )
            )

        lecture.status = LectureStatus.completed
        lecture.finished_at = _utcnow()
        db.commit()
        logger.info("Pipeline completed", extra={"lecture_id": lecture_id})

    except Exception as exc:
        logger.exception("Unhandled pipeline exception")
        db.rollback()
        try:
            lecture = db.get(Lecture, lecture_id)
            if lecture is not None:
                _mark_failed(db, lecture, f"Unhandled error: {exc}")
        except Exception:
            logger.exception("Failed to record failure state for lecture %d", lecture_id)
    finally:
        clear_progress(lecture_id)
        db.close()
