"""Reusable FastAPI dependencies for auth-gated endpoints."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as DBSession

from app.core.db import get_db
from app.models.session import Session
from app.models.user import User

logger = logging.getLogger(__name__)

# `auto_error=False` so we can shape the 401 body ourselves and keep the header handling
# uniform with the rest of the API (plain `{"detail": "..."}` payloads).
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_session(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[DBSession, Depends(get_db)],
) -> Session:
    """Resolve `Authorization: Bearer <token>` → active `Session` ORM row.

    Missing header, malformed scheme, empty token, or unknown token → 401.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    session = db.query(Session).filter(Session.token == credentials.credentials).first()
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return session


def get_current_user(
    session: Annotated[Session, Depends(get_current_session)],
    db: Annotated[DBSession, Depends(get_db)],
) -> User:
    """Resolve the current session's owning user; 401 if the row went missing."""
    user = db.get(User, session.user_id)
    if user is None:
        # Session row survived but user was deleted underneath it; treat as logged out.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return user


# Shared `Depends(get_db)` for endpoints that just want the DB session. Reusing one instance
# is fine and reads a shade better than repeating `Depends(get_db)` inline everywhere.
db_session = Depends(get_db)
