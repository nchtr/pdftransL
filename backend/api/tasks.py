"""Диспатч задач: Celery при USE_CELERY=1, иначе фоновый поток.

Поток держит дев-запуск без брокера (runserver достаточно); продакшен
— celery worker + очередь PDFTRANSL_JOB_QUEUE (тяжёлый GPU-воркер
можно отделить).
"""

from __future__ import annotations

import logging
import threading

from django.conf import settings

from . import services

logger = logging.getLogger(__name__)

try:
    from celery import shared_task

    # Route heavy PDF work to a dedicated queue so an operator can run a
    # GPU/parse worker separately from cheap translation workers:
    #   celery -A config worker -Q pdftransl        (default queue)
    #   celery -A config worker -Q pdftransl_heavy  (GPU box for MinerU)
    @shared_task(bind=True, time_limit=7200, queue="pdftransl")
    def run_translation_task(self, job_id: str) -> str:
        return services.run_job(job_id)

except ImportError:  # celery not installed
    run_translation_task = None


def dispatch_job(job_id: str) -> str:
    """Start job execution; returns the dispatch mode used."""
    if settings.USE_CELERY and run_translation_task is not None:
        queue = getattr(settings, "PDFTRANSL_JOB_QUEUE", "pdftransl")
        run_translation_task.apply_async(args=[job_id], queue=queue)
        return "celery"
    thread = threading.Thread(
        target=services.run_job, args=(job_id,), daemon=True,
        name=f"pdftransl-job-{job_id[:8]}",
    )
    thread.start()
    return "thread"
