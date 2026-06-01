"""Async Celery task for post generation (offloads slow AI calls from request thread)."""
from tasks.celery_app import celery_app


@celery_app.task(name="tasks.generate.generate_post_async", bind=True, max_retries=2)
def generate_post_async(self, post_id: str, request_data: dict) -> dict:
    """Generate a post in the background. Results stored in DB by post_id."""
    # Full implementation wires ContentEngine here once DB layer is connected
    raise NotImplementedError("Wire ContentEngine + DB session here")
