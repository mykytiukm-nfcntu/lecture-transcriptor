"""Map-reduce summary generation over transcript chunks."""
from __future__ import annotations

import json
import logging

from app.schemas.summary import SummaryDocument
from app.schemas.transcript import SegmentDraft
from app.services.chunking import Chunk, format_with_timestamps
from app.services.llm import generate_json, render_prompt

logger = logging.getLogger(__name__)

# Encodes BOTH the map template (`summary_v1.md.j2`) and the reduce template
# (`summary_reduce_v1.md.j2`) as a single version tag persisted on the Summary row.
PROMPT_VERSION = "summary_v1"


def generate(
    chunks: list[Chunk],
    segments: list[SegmentDraft],
    *,
    language: str,
    lecture_title: str,
) -> SummaryDocument:
    """Produce the final `SummaryDocument` from `chunks` via map + reduce."""
    if not chunks:
        # Nothing to summarise. Return an empty document rather than raising: callers can
        # persist it just fine and the API will render an empty summary section.
        return SummaryDocument(title=lecture_title, language=language, sections=[])

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
        partial = generate_json(prompt, schema=SummaryDocument)
        partials.append(partial)

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
    return generate_json(reduce_prompt, schema=SummaryDocument)
