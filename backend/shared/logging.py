"""Centralised structured JSON logging.

Call ``configure_logging(service)`` once at process startup.  All loggers in
the process will emit JSON lines to stdout with at least these fields:

    timestamp   ISO-8601 datetime
    level       DEBUG / INFO / WARNING / ERROR / CRITICAL
    service     value passed to configure_logging()
    logger      dotted Python logger name
    message     human-readable description

Any keyword arguments passed as ``extra={}`` to a logger call are merged into
the JSON object at the top level, so callers can attach arbitrary context:

    logger.info("Crawl page complete", extra={"job_id": job_id, "duration_ms": 42})
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from pythonjsonlogger.jsonlogger import JsonFormatter


class _ServiceJsonFormatter(JsonFormatter):
    """Adds a fixed ``service`` field to every log record."""

    def __init__(self, service: str, *args: Any, **kwargs: Any) -> None:
        self._service = service
        super().__init__(*args, **kwargs)

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["service"] = self._service
        log_record.setdefault("level", record.levelname)
        # Promote any extra keys from the LogRecord that are not standard
        # fields so callers can do logger.info("msg", extra={"job_id": "..."})
        _STANDARD_ATTRS = frozenset(logging.LogRecord(
            "", 0, "", 0, "", (), None
        ).__dict__.keys()) | {"message", "asctime", "levelname", "name"}
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and key not in log_record:
                log_record[key] = value


def configure_logging(service: str, level: int = logging.INFO) -> None:
    """Configure root logger to emit structured JSON to stdout.

    Call once at process startup::

        configure_logging("api")
        configure_logging("worker")
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _ServiceJsonFormatter(
            service=service,
            fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
            rename_fields={"asctime": "timestamp", "name": "logger"},
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
