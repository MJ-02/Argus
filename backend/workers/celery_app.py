from celery import Celery

from shared.config import settings

app = Celery(
    "articlegraph",
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
