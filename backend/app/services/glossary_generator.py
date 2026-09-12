"""Glossary generation grounded in the (possibly elided) whole-transcript context."""
from __future__ import annotations

import logging
from collections.abc import Callable

from app.schemas.glossary import GlossaryDocument
from app.schemas.transcript import SegmentDraft
from app.services.chunking import Chunk, format_with_timestamps
from app.services.llm import generate_json, render_prompt

logger = logging.getLogger(__name__)

PROMPT_VERSION = "glossary_v1"

STEP_COUNT = 1

# Rough character budget for the transcript block inside the prompt. Chosen to fit inside a
# 4-8k token context after the surrounding instructions. If the concatenated timestamped
# transcript exceeds this, we keep the first and last thirds and elide the middle so the model
# still sees intros/wrap-ups where terms are commonly defined.
_MAX_TRANSCRIPT_CHARS = 20_000


def _build_transcript_repr(chunks: list[Chunk], segments: list[SegmentDraft]) -> str:
    per_chunk = [format_with_timestamps(chunk, segments) for chunk in chunks]
    joined = "\n\n".join(block for block in per_chunk if block)
    if len(joined) <= _MAX_TRANSCRIPT_CHARS:
        return joined
    third = _MAX_TRANSCRIPT_CHARS // 3
    return joined[:third] + "\n\n[...omitted...]\n\n" + joined[-third:]


def generate(
    chunks: list[Chunk],
    segments: list[SegmentDraft],
    *,
    language: str,
    lecture_title: str,
    on_step: Callable[[], None] | None = None,
) -> GlossaryDocument:
    """Produce the `GlossaryDocument` (10-30 entries) for the lecture."""
    transcript_repr = _build_transcript_repr(chunks, segments)
    prompt = render_prompt(
        "glossary_v1.md.j2",
        language=language,
        transcript_with_timestamps=transcript_repr,
        lecture_title=lecture_title,
    )
    logger.info("Glossary generation", extra={"chunk_count": len(chunks)})
    result = generate_json(prompt, schema=GlossaryDocument)
    if on_step is not None:
        try:
            on_step()
        except Exception:  # noqa: BLE001 - progress reporting must never break generation.
            logger.exception("Glossary progress callback raised; ignoring")
    return result
