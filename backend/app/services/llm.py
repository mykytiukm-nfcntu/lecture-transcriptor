"""Ollama HTTP client with strict-JSON generation, schema validation, and bounded retries."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, TypeVar

import httpx
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class LlmGenerationError(RuntimeError):
    """Raised when the LLM adapter fails after exhausting its retry budget."""

    def __init__(self, message: str, *, last_exception: Exception | None = None) -> None:
        super().__init__(message)
        self.last_exception = last_exception


_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_env: Environment | None = None
_env_lock = threading.Lock()

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _get_env() -> Environment:
    global _env
    with _env_lock:
        if _env is None:
            _env = Environment(
                loader=FileSystemLoader(str(_PROMPTS_DIR)),
                undefined=StrictUndefined,
                keep_trailing_newline=True,
                autoescape=False,
            )
        return _env


def _get_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            settings = get_settings()
            _client = httpx.Client(
                base_url=settings.OLLAMA_BASE_URL,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
        return _client


def close_client() -> None:
    """Close and drop the shared HTTP client (idempotent).

    Called from the FastAPI lifespan shutdown hook.
    """
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                logger.exception("Error closing LLM HTTP client")
            finally:
                _client = None


def render_prompt(template_name: str, **kwargs: Any) -> str:
    """Render a Jinja2 prompt template from `backend/app/prompts/`."""
    return _get_env().get_template(template_name).render(**kwargs)


T = TypeVar("T", bound=BaseModel)


def generate_json(prompt: str, *, schema: type[T], model: str | None = None) -> T:
    """POST `prompt` to Ollama in JSON mode, parse the response, validate against `schema`.

    Retries up to `settings.LLM_MAX_RETRIES` additional times on transport, JSON-parse, or
    schema-validation failure. The prompt is not modified between attempts. On final failure,
    raises `LlmGenerationError` with the last exception attached.
    """
    settings = get_settings()
    client = _get_client()
    payload: dict[str, Any] = {
        "model": model or settings.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }

    max_attempts = settings.LLM_MAX_RETRIES + 1
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.post("/api/generate", json=payload)
            response.raise_for_status()
            body = response.json()
            content = body.get("response", "")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty `response` field in Ollama reply")
            data = json.loads(content)
            return schema.model_validate(data)
        except (httpx.HTTPError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_exc = exc
            if attempt < max_attempts:
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                continue

    message = f"LLM generation failed after {max_attempts} attempts: {last_exc}"
    raise LlmGenerationError(message, last_exception=last_exc) from last_exc


def list_models() -> list[str]:
    """Best-effort listing of models available on the Ollama server. Never raises."""
    try:
        response = _get_client().get("/api/tags")
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Ollama /api/tags failed: %s", exc)
        return []
    models = data.get("models", []) if isinstance(data, dict) else []
    names: list[str] = []
    for entry in models:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            names.append(entry["name"])
    return names
