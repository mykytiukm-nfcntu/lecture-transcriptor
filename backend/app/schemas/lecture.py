"""Lecture list / detail / status response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.lecture import LectureStatus


class LectureListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    original_filename: str
    duration_seconds: float | None
    language: str | None
    status: LectureStatus
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class LectureDetail(LectureListItem):
    course_id: int


class LectureStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: LectureStatus
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    # All computed by the API layer, not mapped columns.
    elapsed_seconds: float | None
    preliminary_eta_seconds: float | None
    progress_percent: float | None = None
    progress_stage: str | None = None
