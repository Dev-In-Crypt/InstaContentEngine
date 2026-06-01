from celery import Celery
from config import get_settings

settings = get_settings()

celery_app = Celery(
    "insta_engine",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["tasks.generate", "tasks.publish"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)
