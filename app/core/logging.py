"""JSON structured logging with per-request contextvar threading.

Every log record gets `request_id`, `path`, `method`, `user_id` (when known),
so grepping a single request across services is a one-liner."""
from __future__ import annotations

import contextvars
import json
import logging
import sys
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "user_id", default=None
)
path_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "path", default=None
)
method_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "method", default=None
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, var in (
            ("request_id", request_id_var),
            ("user_id", user_id_var),
            ("path", path_var),
            ("method", method_var),
        ):
            value = var.get()
            if value is not None:
                payload[key] = value
        for k, v in record.__dict__.items():
            if k.startswith("ctx_"):
                payload[k[4:]] = v
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in ("sqlalchemy.engine", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
