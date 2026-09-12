"""GET /api/models — list installed Ollama models + the current default."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.models.user import User
from app.services import llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelsResponse(BaseModel):
    """Reply for `GET /api/models`."""

    default: str
    installed: list[str]


@router.get("", response_model=ModelsResponse)
def list_installed_models(
    _user: Annotated[User, Depends(get_current_user)],
) -> ModelsResponse:
    """List Ollama models present on the local server. Auth-gated but user-scoped-agnostic."""
    settings = get_settings()
    return ModelsResponse(default=settings.OLLAMA_MODEL, installed=llm.list_models())
