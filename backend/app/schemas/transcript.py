"""Transcript response schemas plus the ASR-side draft segment."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SegmentDraft(BaseModel):
    """Segment produced by the ASR service before it is persisted to the DB."""

    index: int = Field(ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str
    confidence: float | None = None


class TranscriptSegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None


class TranscriptResponse(BaseModel):
    language: str
    full_text: str
    segments: list[TranscriptSegmentResponse]
