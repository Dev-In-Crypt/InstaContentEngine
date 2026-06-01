"""Async Celery task for scheduled Instagram publishing."""
from tasks.celery_app import celery_app


@celery_app.task(name="tasks.publish.publish_post_async", bind=True, max_retries=3)
def publish_post_async(self, post_id: str) -> dict:
    """Publish a scheduled post. Called by Celery beat at the right time."""
    raise NotImplementedError("Wire InstagramPublisher + DB session here")
