"""Environment-driven application settings, exposed via a cached `get_settings()`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from the process environment or a `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_ENV: str = "local"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "sqlite:///./storage/app.db"
    STORAGE_ROOT: str = "./storage"
    MEDIA_SUBDIR: str = "media"

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "gemma4:12b"
    # Per-call Ollama HTTP timeout. Large local models on CPU can need 10–20 minutes
    # per chunk; the pipeline is single-job locked and retryable, so a generous ceiling
    # is cheaper than a spurious LlmGenerationError.
    LLM_TIMEOUT_SECONDS: int = 1800
    LLM_MAX_RETRIES: int = 2

    CHUNK_TOKENS: int = 1800
    CHUNK_OVERLAP_TOKENS: int = 200

    WHISPER_MODEL_SIZE: Literal["tiny", "base", "small", "medium", "large-v3"] = "medium"
    WHISPER_DEVICE: Literal["cpu", "cuda", "auto"] = "auto"
    WHISPER_COMPUTE_TYPE: str = "int8"
    WHISPER_VAD: bool = True
    WHISPER_FALLBACK_LANGUAGE: str = "uk"
    WHISPER_LANGUAGE_DETECT_MIN_PROB: float = 0.5
    # 0 = auto (all logical cores). Otherwise pin to the given count.
    WHISPER_CPU_THREADS: int = 0

    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_csv_origins(cls, value: object) -> object:
        # Env vars arrive as a comma-separated string; the default is already a list.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def media_root(self) -> Path:
        return Path(self.STORAGE_ROOT) / self.MEDIA_SUBDIR


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide `Settings` singleton, built on first access."""
    return Settings()
