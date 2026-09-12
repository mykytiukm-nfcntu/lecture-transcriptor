"""Glossary schemas: the strict LLM-output shape + the API response wrapper."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GlossaryEntry(BaseModel):
    term: str = Field(min_length=1, max_length=255)
    # Definition is 1-3 sentences; length bound is a proxy for that.
    definition: str = Field(min_length=1, max_length=800)
    first_mention_seconds: float = Field(ge=0)


class GlossaryDocument(BaseModel):
    language: str
    entries: list[GlossaryEntry] = Field(min_length=10, max_length=30)


class GlossaryTermResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    term: str
    definition: str
    first_mention_seconds: float


class GlossaryResponse(BaseModel):
    language: str
    prompt_version: str
    entries: list[GlossaryTermResponse]
    created_at: datetime
