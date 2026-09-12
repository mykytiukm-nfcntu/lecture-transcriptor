"""Auth request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]+$"


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=_USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=_USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    token: str
    token_type: Literal["bearer"] = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime
