"""JSON logs with a request context and secret redaction."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from app.core.redact import redact

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
job_id_var: ContextVar[str] = ContextVar("job_id", default="")
project_id_var: ContextVar[str] = ContextVar("project_id", default="")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "service": "api",
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": request_id_var.get(),
            "job_id": job_id_var.get(),
            "project_id": project_id_var.get(),
        }
        if hasattr(record, "operation"):
            payload["operation"] = record.operation
        if record.exc_info:
            payload["error_type"] = getattr(record.exc_info[0], "__name__", "Exception")
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("celery").setLevel(level.upper())
