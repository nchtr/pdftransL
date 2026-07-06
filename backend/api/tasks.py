"""Job dispatch: Celery when configured, background threads otherwise.

Thread mode keeps the dev setup broker-free (`python manage.py
runserver` is enough); production should set USE_CELERY=1 and run a
worker for durability and horizontal scaling.
"""

from __future__ import annotations

import logging
import threading

from django.conf import settings

from . import services

logger = logging.getLogger(__name__)

try:
    from celery import shared_task

    @shared_task(bind=True, time_limit=3600)
    def run_translation_task(self, job_id: str) -> str:
        return services.run_job(job_id)

except ImportError:  # celery not installed
    run_translation_task = None


def dispatch_job(job_id: str) -> str:
    """Start job execution; returns the dispatch mode used."""
    if settings.USE_CELERY and run_translation_task is not None:
        run_translation_task.delay(job_id)
        return "celery"
    thread = threading.Thread(
        target=services.run_job, args=(job_id,), daemon=True,
        name=f"pdftransl-job-{job_id[:8]}",
    )
    thread.start()
    return "thread"
