"""Auth routes: register, login (with global session eviction), logout, me."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session as DBSession

from app.api.deps import get_current_session, get_current_user
from app.core.db import get_db
from app.core.security import generate_token, hash_password, verify_password
from app.models.session import Session
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    db: Annotated[DBSession, Depends(get_db)],
) -> User:
    """Create a new account. Duplicate username → 409."""
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="username already taken")
    user = User(username=payload.username, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("user registered", extra={"user_id": user.id})
    return user


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    db: Annotated[DBSession, Depends(get_db)],
) -> TokenResponse:
    """Verify credentials, evict every existing session, mint a fresh token."""
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    # "Only one active user at a time" invariant: wipe every row before inserting the new one.
    db.query(Session).delete(synchronize_session=False)

    session = Session(user_id=user.id, token=generate_token())
    db.add(session)
    db.commit()
    logger.info("login succeeded", extra={"user_id": user.id})
    return TokenResponse(token=session.token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    session: Annotated[Session, Depends(get_current_session)],
    db: Annotated[DBSession, Depends(get_db)],
) -> Response:
    """Delete only the caller's session row."""
    db.delete(session)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
def me(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Return the currently authenticated user."""
    return user
