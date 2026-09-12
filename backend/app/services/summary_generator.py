"""Map-reduce summary generation over transcript chunks."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

from app.schemas.summary import SummaryDocument
from app.schemas.transcript import SegmentDraft
from app.services.chunking import Chunk, format_with_timestamps
from app.services.llm import generate_json, render_prompt

logger = logging.getLogger(__name__)

# Encodes BOTH the map template (`summary_v1.md.j2`) and the reduce template
# (`summary_reduce_v1.md.j2`) as a single version tag persisted on the Summary row.
PROMPT_VERSION = "summary_v1"


def step_count(chunks: list[Chunk]) -> int:
    """Number of LLM calls `generate` will make for the given chunk list."""
    if not chunks:
        return 0
    if len(chunks) == 1:
        return 1
    return len(chunks) + 1


def generate(
    chunks: list[Chunk],
    segments: list[SegmentDraft],
    *,
    language: str,
    lecture_title: str,
    on_step: Callable[[], None] | None = None,
    model: str | None = None,
) -> SummaryDocument:
    """Produce the final `SummaryDocument` from `chunks` via map + reduce."""
    if not chunks:
        # Nothing to summarise. Return an empty document rather than raising: callers can
        # persist it just fine and the API will render an empty summary section.
        return SummaryDocument(title=lecture_title, language=language, sections=[])

    def _tick() -> None:
        if on_step is None:
            return
        try:
            on_step()
        except Exception:  # noqa: BLE001 - progress reporting must never break generation.
            logger.exception("Summary progress callback raised; continuing")

    partials: list[SummaryDocument] = []
    for idx, chunk in enumerate(chunks):
        chunk_text = format_with_timestamps(chunk, segments)
        prompt = render_prompt(
            "summary_v1.md.j2",
            language=language,
            chunk_text=chunk_text,
        )
        logger.info(
            "Summary map step",
            extra={"chunk_index": idx, "chunk_count": len(chunks)},
        )
        partial = generate_json(prompt, schema=SummaryDocument, model=model)
        partials.append(partial)
        _tick()

    if len(partials) == 1:
        return partials[0]

    partials_json = json.dumps(
        [p.model_dump(mode="json") for p in partials],
        ensure_ascii=False,
    )
    reduce_prompt = render_prompt(
        "summary_reduce_v1.md.j2",
        language=language,
        partials_json=partials_json,
        lecture_title=lecture_title,
    )
    logger.info("Summary reduce step", extra={"partial_count": len(partials)})
    result = generate_json(reduce_prompt, schema=SummaryDocument, model=model)
    _tick()
    return result
