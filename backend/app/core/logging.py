"""Structured JSON logging with per-request / per-job correlation-id context."""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Standard LogRecord attributes we never emit as user "extras"; anything else on
# `record.__dict__` is treated as caller-supplied context.
_STANDARD_LOGRECORD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
        "correlation_id",
    }
)


class CorrelationIdFilter(logging.Filter):
    """Copy the current `correlation_id` ContextVar onto every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = record.stack_info
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOGRECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = _coerce_json_safe(value)
        return json.dumps(payload, ensure_ascii=False, default=_coerce_json_safe)


def _coerce_json_safe(value: Any) -> Any:
    """Return a value that `json.dumps` can serialise, falling back to `repr()`."""
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def configure_logging(level: str) -> None:
    """Install the JSON formatter and correlation-id filter on the root logger."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationIdFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
