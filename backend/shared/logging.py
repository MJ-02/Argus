import logging
import sys
from typing import Any

from pythonjsonlogger.jsonlogger import JsonFormatter


class _ServiceJsonFormatter(JsonFormatter):
    """Adds a fixed `service` field to every log record."""

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


def configure_logging(service: str, level: int = logging.INFO) -> None:
    """
    Configure root logger to emit structured JSON to stdout.

    Call once at process startup:
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

    # Silence overly chatty third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
