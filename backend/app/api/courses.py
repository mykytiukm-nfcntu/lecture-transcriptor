"""Course routes: create / list / detail / delete, all scoped to the current user."""
from __future__ import annotations

import logging
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.models.course import Course
from app.models.lecture import Lecture
from app.models.user import User
from app.schemas.course import CourseCreate, CourseResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/courses", tags=["courses"])


@router.post("", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
def create_course(
    payload: CourseCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> CourseResponse:
    """Create a course owned by the current user."""
    course = Course(user_id=user.id, title=payload.title)
    db.add(course)
    db.commit()
    db.refresh(course)
    return CourseResponse(
        id=course.id,
        title=course.title,
        created_at=course.created_at,
        lecture_count=0,
    )


@router.get("", response_model=list[CourseResponse])
def list_courses(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> list[CourseResponse]:
    """List the current user's courses with a lecture count per row."""
    counts_subq = (
        select(Lecture.course_id.label("course_id"), func.count(Lecture.id).label("cnt"))
        .group_by(Lecture.course_id)
        .subquery()
    )
    rows = (
        db.query(Course, func.coalesce(counts_subq.c.cnt, 0).label("lecture_count"))
        .outerjoin(counts_subq, counts_subq.c.course_id == Course.id)
        .filter(Course.user_id == user.id)
        .order_by(Course.created_at.desc(), Course.id.desc())
        .all()
    )
    return [
        CourseResponse(
            id=course.id,
            title=course.title,
            created_at=course.created_at,
            lecture_count=int(lecture_count),
        )
        for course, lecture_count in rows
    ]


@router.get("/{course_id}", response_model=CourseResponse)
def get_course(
    course_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> CourseResponse:
    """Return one course; non-owner or missing → 404 (never 403)."""
    course = (
        db.query(Course)
        .filter(Course.id == course_id, Course.user_id == user.id)
        .first()
    )
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="course not found")
    lecture_count = (
        db.query(func.count(Lecture.id)).filter(Lecture.course_id == course.id).scalar() or 0
    )
    return CourseResponse(
        id=course.id,
        title=course.title,
        created_at=course.created_at,
        lecture_count=int(lecture_count),
    )


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(
    course_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
) -> Response:
    """Delete a course, cascade its lectures/artifacts at the DB level, and sweep media dirs."""
    course = (
        db.query(Course)
        .filter(Course.id == course_id, Course.user_id == user.id)
        .first()
    )
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="course not found")
    # Capture lecture ids before the DB cascade wipes them; disk cleanup runs after commit.
    lecture_ids = [lecture.id for lecture in course.lectures]
    user_id = course.user_id
    db.delete(course)
    db.commit()
    # Cascade only touches DB rows, so sweep each lecture's media folder mirroring the per-lecture DELETE.
    settings = get_settings()
    for lect_id in lecture_ids:
        folder = settings.media_root / str(user_id) / str(lect_id)
        shutil.rmtree(folder, ignore_errors=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
