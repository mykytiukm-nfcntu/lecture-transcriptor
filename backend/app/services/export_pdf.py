"""ReportLab PDF export using the bundled DejaVuSans TTF for Cyrillic coverage."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.models.lecture import Lecture
from app.models.transcript import Transcript
from app.schemas.summary import SummaryDocument, SummarySection
from app.services.export_txt import GlossaryLike, format_timestamp

logger = logging.getLogger(__name__)

_FONT_NAME = "DejaVuSans"
_FONT_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "DejaVuSans.ttf"

_font_registered = False


def _try_register_font() -> None:
    """Register the bundled Unicode TTF. Silent success; caller decides how to handle failure."""
    global _font_registered
    if _font_registered:
        return
    if not _FONT_PATH.exists():
        return
    try:
        pdfmetrics.registerFont(TTFont(_FONT_NAME, str(_FONT_PATH)))
        _font_registered = True
    except Exception as exc:
        logger.warning("Failed to register PDF font %s: %s", _FONT_PATH, exc)


# Attempt registration at import time. If the file is missing we only warn - unit tests that
# never render PDFs must still be able to import this module cleanly.
if not _FONT_PATH.exists():
    logger.warning(
        "PDF font missing at %s. PDF export will fail until the file is placed. "
        "See README for install instructions.",
        _FONT_PATH,
    )
else:
    _try_register_font()


def _ensure_font_registered() -> None:
    _try_register_font()
    if not _font_registered:
        raise RuntimeError(
            f"PDF export requires the DejaVuSans TTF at {_FONT_PATH}. "
            "See README for install instructions."
        )


def _build_styles() -> dict[str, ParagraphStyle]:
    base = {"fontName": _FONT_NAME}
    return {
        "title": ParagraphStyle("Title", fontSize=18, leading=22, spaceAfter=12, **base),
        "meta": ParagraphStyle("Meta", fontSize=10, leading=13, spaceAfter=2, **base),
        "h1": ParagraphStyle(
            "H1", fontSize=14, leading=18, spaceBefore=12, spaceAfter=8, **base
        ),
        "section": ParagraphStyle(
            "Section", fontSize=12, leading=16, spaceBefore=8, spaceAfter=4, **base
        ),
        "bullet": ParagraphStyle("Bullet", fontSize=10, leading=14, leftIndent=12, **base),
        "highlight": ParagraphStyle(
            "Highlight", fontSize=10, leading=14, leftIndent=12, textColor="#555555", **base
        ),
        "body": ParagraphStyle("Body", fontSize=10, leading=14, **base),
    }


def _escape(text: str) -> str:
    """Escape XML-ish markup that ReportLab's Paragraph would otherwise try to parse."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _render_section_pdf(
    section: SummarySection,
    level: int,
    flow: list,
    styles: dict[str, ParagraphStyle],
) -> None:
    indent = "&nbsp;" * (level * 4)
    heading = (
        f"{indent}<b>{_escape(section.heading)}</b> "
        f"[{format_timestamp(section.timestamp_seconds)}]"
    )
    flow.append(Paragraph(heading, styles["section"]))
    for bullet in section.bullets:
        flow.append(Paragraph(f"{indent}&bull; {_escape(bullet)}", styles["bullet"]))
    for highlight in section.highlights:
        flow.append(
            Paragraph(
                f"{indent}[{highlight.kind} {format_timestamp(highlight.timestamp_seconds)}] "
                f"{_escape(highlight.text)}",
                styles["highlight"],
            )
        )
    for sub in section.subsections:
        _render_section_pdf(sub, level + 1, flow, styles)


def render_pdf(
    lecture: Lecture,
    transcript: Transcript | None,
    summary_doc: SummaryDocument | None,
    glossary_entries: Sequence[GlossaryLike] | None,
) -> bytes:
    """Render `lecture` and its artifacts to a PDF, returning the raw bytes."""
    _ensure_font_registered()

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=lecture.title,
    )
    styles = _build_styles()
    flow: list = []

    flow.append(Paragraph(_escape(lecture.title), styles["title"]))
    flow.append(Paragraph(f"Date: {lecture.created_at.date().isoformat()}", styles["meta"]))
    if lecture.duration_seconds is not None:
        flow.append(
            Paragraph(
                f"Duration: {format_timestamp(lecture.duration_seconds)}", styles["meta"]
            )
        )
    if lecture.language:
        flow.append(Paragraph(f"Language: {_escape(lecture.language)}", styles["meta"]))
    flow.append(Spacer(1, 12))

    if summary_doc is not None and summary_doc.sections:
        flow.append(Paragraph("Summary", styles["h1"]))
        for section in summary_doc.sections:
            _render_section_pdf(section, level=0, flow=flow, styles=styles)
        flow.append(Spacer(1, 12))

    if glossary_entries:
        flow.append(Paragraph("Glossary", styles["h1"]))
        for entry in glossary_entries:
            ts = format_timestamp(entry.first_mention_seconds)
            flow.append(
                Paragraph(
                    f"<b>{_escape(entry.term)}</b> ({ts}): {_escape(entry.definition)}",
                    styles["body"],
                )
            )
        flow.append(Spacer(1, 12))

    if transcript is not None:
        flow.append(Paragraph("Transcript", styles["h1"]))
        for seg in transcript.segments:
            flow.append(
                Paragraph(
                    f"[{format_timestamp(seg.start_seconds)}] {_escape(seg.text)}",
                    styles["body"],
                )
            )

    doc.build(flow)
    return buf.getvalue()
