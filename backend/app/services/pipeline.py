"""Per-lecture orchestration: media -> ASR -> chunking -> LLM -> persistence."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import correlation_id
from app.core.progress import clear_progress, set_progress, set_stage
from app.models.glossary import GlossaryTerm
from app.models.lecture import Lecture, LectureStatus, StageCheckpoint
from app.models.summary import Summary
from app.models.transcript import Transcript, TranscriptSegment
from app.schemas.transcript import SegmentDraft
from app.services import asr, chunking, glossary_generator, llm, media, summary_generator

logger = logging.getLogger(__name__)


# Ordinal for the `last_completed_stage` watermark; consumed by `_reached`.
_STAGE_ORDER: dict[StageCheckpoint, int] = {
    StageCheckpoint.normalize: 1,
    StageCheckpoint.transcribe: 2,
    StageCheckpoint.summary: 3,
    StageCheckpoint.glossary: 4,
}


def _reached(watermark: StageCheckpoint | None, stage: StageCheckpoint) -> bool:
    """True when the checkpoint watermark is at least at `stage`."""
    if watermark is None:
        return False
    return _STAGE_ORDER[watermark] >= _STAGE_ORDER[stage]


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Preserves `last_completed_stage` so a subsequent retry resumes from the last committed stage.
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


def process_lecture(
    lecture_id: int,
    generation_language: str | None = None,
    model: str | None = None,
) -> None:
    """Run the full pipeline for one lecture. Never raises - failure is stored on the row.

    Resumable via `Lecture.last_completed_stage`: on entry, stages whose checkpoint has
    already been committed are skipped and their artifacts are reused. Stages that are
    re-run first delete any orphaned artifacts left by a prior crash.

    `generation_language`, if provided, replaces the ASR-detected language when calling
    the summary and glossary generators and is what gets persisted on those artifact
    rows. `lecture.language` always stores the audio language detected by ASR.

    `model`, if provided, overrides `settings.OLLAMA_MODEL` for the LLM calls in this run.
    """
    correlation_id.set(f"lecture-{lecture_id}")
    logger.info("Pipeline start", extra={"lecture_id": lecture_id})

    db: DBSession = SessionLocal()
    try:
        lecture = db.get(Lecture, lecture_id)
        if lecture is None:
            logger.error("Lecture %d not found; aborting pipeline", lecture_id)
            return

        # Defensive: the retry endpoint refuses this in Wave 3, but if we somehow land
        # here with every stage already committed, do nothing rather than duplicate work.
        if lecture.last_completed_stage == StageCheckpoint.glossary:
            logger.error(
                "Pipeline entered with glossary checkpoint already committed; refusing to re-run",
                extra={"lecture_id": lecture_id},
            )
            return

        checkpoint = lecture.last_completed_stage
        settings = get_settings()
        media_dir = settings.media_root / str(lecture.user_id) / str(lecture.id)
        normalized_path = media_dir / "normalized.wav"

        lecture.started_at = _utcnow()
        lecture.error_message = None
        db.commit()

        # ------------------------------------------------------------------
        # Stage 1: normalise.
        # ------------------------------------------------------------------
        if _reached(checkpoint, StageCheckpoint.normalize) and normalized_path.exists():
            # Reuse the previously normalised file; skip ffmpeg.
            pass
        else:
            if _reached(checkpoint, StageCheckpoint.normalize):
                logger.warning(
                    "Normalize checkpoint recorded but normalized.wav missing; "
                    "re-running normalise as a safety net",
                    extra={"lecture_id": lecture_id, "path": str(normalized_path)},
                )

            original_path = _find_original(lecture.user_id, lecture.id)
            if original_path is None:
                _mark_failed(db, lecture, "Original media file not found on disk")
                return

            lecture.status = LectureStatus.normalizing
            db.commit()
            set_progress(lecture_id, 0.0)

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

            lecture.last_completed_stage = StageCheckpoint.normalize
            lecture.status = LectureStatus.transcribing
            db.commit()

        # ------------------------------------------------------------------
        # Stage 2: transcribe.
        # ------------------------------------------------------------------
        segment_drafts: list[SegmentDraft]
        detected_language: str

        if _reached(checkpoint, StageCheckpoint.transcribe):
            transcript_row = (
                db.query(Transcript).filter(Transcript.lecture_id == lecture.id).one_or_none()
            )
            if transcript_row is None:
                _mark_failed(
                    db,
                    lecture,
                    "Transcript checkpoint reached but transcript rows are missing",
                )
                return
            segment_drafts = [
                SegmentDraft(
                    index=seg.index,
                    start_seconds=seg.start_seconds,
                    end_seconds=seg.end_seconds,
                    text=seg.text,
                    confidence=seg.confidence,
                )
                for seg in transcript_row.segments
            ]
            detected_language = transcript_row.language
        else:
            if lecture.status != LectureStatus.transcribing:
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

            # Delete any Transcript orphan (TranscriptSegments cascade) only now that ASR
            # succeeded, so a failed regeneration above leaves the prior row on disk.
            existing_transcript = (
                db.query(Transcript).filter(Transcript.lecture_id == lecture.id).one_or_none()
            )
            if existing_transcript is not None:
                db.delete(existing_transcript)
                db.flush()

            full_text = asr.join_full_text(segment_drafts)
            transcript_row = Transcript(
                lecture_id=lecture.id,
                full_text=full_text,
                language=detected_language,
            )
            db.add(transcript_row)
            db.flush()
            for draft in segment_drafts:
                db.add(
                    TranscriptSegment(
                        transcript_id=transcript_row.id,
                        index=draft.index,
                        start_seconds=draft.start_seconds,
                        end_seconds=draft.end_seconds,
                        text=draft.text,
                        confidence=draft.confidence,
                    )
                )
            lecture.language = detected_language
            lecture.last_completed_stage = StageCheckpoint.transcribe
            lecture.status = LectureStatus.generating
            db.commit()

        # ------------------------------------------------------------------
        # Chunking + language resolution feed both LLM stages.
        # ------------------------------------------------------------------
        chunks = chunking.chunk_segments(
            segment_drafts,
            target_tokens=settings.CHUNK_TOKENS,
            overlap_tokens=settings.CHUNK_OVERLAP_TOKENS,
        )
        resolved_language = generation_language or detected_language

        # ------------------------------------------------------------------
        # Stage 3: summary. LLM generation is two coarse steps; the frontend renders
        # these as a "Step 1 of 2" / "Step 2 of 2" indicator via `set_stage`.
        # ------------------------------------------------------------------
        if not _reached(checkpoint, StageCheckpoint.summary):
            if lecture.status != LectureStatus.generating:
                lecture.status = LectureStatus.generating
                db.commit()

            set_stage(lecture_id, "summary")
            try:
                summary_doc = summary_generator.generate(
                    chunks,
                    segment_drafts,
                    language=resolved_language,
                    lecture_title=lecture.title,
                    model=model,
                )
            except llm.LlmGenerationError as exc:
                _mark_failed(db, lecture, f"Summary generation failed: {exc}")
                return

            # Delete any leftover Summary only now that the generator succeeded, so a
            # failed regeneration above leaves the prior row on disk.
            existing_summary = (
                db.query(Summary).filter(Summary.lecture_id == lecture.id).one_or_none()
            )
            if existing_summary is not None:
                db.delete(existing_summary)
                db.flush()

            db.add(
                Summary(
                    lecture_id=lecture.id,
                    content_json=summary_doc.model_dump_json(),
                    prompt_version=summary_generator.PROMPT_VERSION,
                    generation_language=resolved_language,
                )
            )
            lecture.last_completed_stage = StageCheckpoint.summary
            db.commit()

        # ------------------------------------------------------------------
        # Stage 4: glossary. Never skipped here: entry with `>= glossary` returns early.
        # ------------------------------------------------------------------
        if lecture.status != LectureStatus.generating:
            lecture.status = LectureStatus.generating
            db.commit()

        set_stage(lecture_id, "glossary")
        try:
            glossary_doc = glossary_generator.generate(
                chunks,
                segment_drafts,
                language=resolved_language,
                lecture_title=lecture.title,
                model=model,
            )
        except llm.LlmGenerationError as exc:
            _mark_failed(db, lecture, f"Glossary generation failed: {exc}")
            return

        # Delete any leftover GlossaryTerms only now that the generator succeeded, so a
        # failed regeneration above leaves the prior rows on disk.
        existing_terms = db.query(GlossaryTerm).filter(GlossaryTerm.lecture_id == lecture.id).all()
        for term in existing_terms:
            db.delete(term)
        if existing_terms:
            db.flush()

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

        lecture.last_completed_stage = StageCheckpoint.glossary
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
