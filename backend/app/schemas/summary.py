"""Summary schemas: the strict LLM-output shape + the API response wrapper."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SummaryHighlight(BaseModel):
    kind: Literal["definition", "formula", "example", "key_point"]
    text: str
    timestamp_seconds: float = Field(ge=0)


class SummarySection(BaseModel):
    heading: str
    timestamp_seconds: float = Field(ge=0)
    bullets: list[str]
    highlights: list[SummaryHighlight] = []
    subsections: list["SummarySection"] = []


class SummaryDocument(BaseModel):
    title: str
    language: str
    sections: list[SummarySection]


class SummaryResponse(BaseModel):
    content: SummaryDocument
    prompt_version: str
    generation_language: str
    created_at: datetime


# Resolve the self-reference on `SummarySection.subsections`.
SummarySection.model_rebuild()
