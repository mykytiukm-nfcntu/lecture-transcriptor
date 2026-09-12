"""Course request/response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class CourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    # Populated by the API layer via a subquery / counted relationship — not a mapped column.
    lecture_count: int = Field(ge=0)
