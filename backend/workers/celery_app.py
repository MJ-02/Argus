from celery import Celery
from celery.signals import worker_process_init

from shared.config import settings
from shared.logging import configure_logging

app = Celery(
    "argus",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["workers.tasks"],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)


@worker_process_init.connect
def _init_logging(**kwargs: object) -> None:
    """Configure structured JSON logging when each worker process starts."""
    configure_logging("worker")
