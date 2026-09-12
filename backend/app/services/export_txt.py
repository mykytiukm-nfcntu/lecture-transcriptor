"""Plain-text export of a completed lecture: header, summary, glossary, transcript."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from app.models.lecture import Lecture
from app.models.transcript import Transcript
from app.schemas.glossary import GlossaryEntry, GlossaryTermResponse
from app.schemas.summary import SummaryDocument, SummarySection

GlossaryLike = Union[GlossaryTermResponse, GlossaryEntry]


def format_timestamp(seconds: float) -> str:
    """Return `HH:MM:SS` for a non-negative seconds value."""
    total = int(round(max(0.0, seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _render_section(section: SummarySection, level: int, lines: list[str]) -> None:
    indent = "  " * level
    lines.append(f"{indent}{section.heading}  [{format_timestamp(section.timestamp_seconds)}]")
    for bullet in section.bullets:
        lines.append(f"{indent}  - {bullet}")
    for highlight in section.highlights:
        lines.append(
            f"{indent}  * [{highlight.kind} {format_timestamp(highlight.timestamp_seconds)}] "
            f"{highlight.text}"
        )
    for sub in section.subsections:
        _render_section(sub, level + 1, lines)


def render_txt(
    lecture: Lecture,
    transcript: Transcript | None,
    summary_doc: SummaryDocument | None,
    glossary_entries: Sequence[GlossaryLike] | None,
) -> str:
    """Return the full plain-text export for `lecture`."""
    lines: list[str] = []
    lines.append(f"# {lecture.title}")
    lines.append(f"Date: {lecture.created_at.date().isoformat()}")
    if lecture.duration_seconds is not None:
        lines.append(f"Duration: {format_timestamp(lecture.duration_seconds)}")
    if lecture.language:
        lines.append(f"Language: {lecture.language}")

    if summary_doc is not None and summary_doc.sections:
        lines.append("")
        lines.append("## Summary")
        for section in summary_doc.sections:
            _render_section(section, level=0, lines=lines)

    if glossary_entries:
        lines.append("")
        lines.append("## Glossary")
        for entry in glossary_entries:
            ts = format_timestamp(entry.first_mention_seconds)
            lines.append(f"- {entry.term}: {entry.definition}  ({ts})")

    if transcript is not None:
        lines.append("")
        lines.append("## Transcript")
        for seg in transcript.segments:
            lines.append(f"[{format_timestamp(seg.start_seconds)}] {seg.text}")

    return "\n".join(lines) + "\n"
