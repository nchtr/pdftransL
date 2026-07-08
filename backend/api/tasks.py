"""Job dispatch: Celery when configured, background threads otherwise.

Thread mode keeps the dev setup broker-free (`python manage.py
runserver` is enough); production should set USE_CELERY=1 and run a
worker for durability and horizontal scaling.
"""

from __future__ import annotations

import atexit
import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import close_old_connections

from . import services
from .models import TranslationJob

logger = logging.getLogger(__name__)

# Глобальный пул потоков для fallback-режима (без Celery).
_local_thread_pool = ThreadPoolExecutor(
    max_workers=getattr(settings, "LOCAL_WORKER_THREADS", 2),
    thread_name_prefix="pdftransl-worker"
)

# Хак для ограничения размера внутренней очереди ThreadPoolExecutor (чтобы избежать OOM).
# По умолчанию у него нет лимита, и миллион задач съест всю RAM.
MAX_QUEUE_SIZE = getattr(settings, "LOCAL_WORKER_QUEUE_LIMIT", 10)

def _shutdown_pool():
    """Корректное завершение потоков при остановке сервера/перезагрузке."""
    logger.info("Shutting down local thread pool...")
    # Не ждем (wait=False), просто запрещаем новые таски, 
    # чтобы не вешать процесс остановки Django.
    _local_thread_pool.shutdown(wait=False, cancel_futures=True)

atexit.register(_shutdown_pool)


try:
    from celery import shared_task

    @shared_task(bind=True, time_limit=7200, queue="pdftransl")
    def run_translation_task(self, job_id: str) -> str:
        try:
            return services.run_job(job_id)
        except Exception as exc:
            logger.exception("Celery task critically failed for job %s: %s", job_id, exc)
            _mark_job_as_failed(job_id, str(exc))
            raise

except ImportError:  # celery not installed
    run_translation_task = None


def _mark_job_as_failed(job_id: str, error_msg: str):
    """Аварийный перевод задачи в статус FAILED при жестких крэшах потока/воркера."""
    try:
        job = TranslationJob.objects.get(pk=job_id)
        if job.status not in (TranslationJob.Status.COMPLETED, TranslationJob.Status.FAILED):
            job.status = TranslationJob.Status.FAILED
            job.error = f"Critical worker failure: {error_msg}"
            job.save(update_fields=["status", "error"])
    except Exception as db_exc:
        logger.error("Could not mark job %s as failed: %s", job_id, db_exc)


def _run_job_in_thread(job_id: str):
    """Обертка для безопасного выполнения в пуле потоков с закрытием коннектов БД."""
    try:
        services.run_job(job_id)
    except Exception as exc:
        logger.exception("Local worker thread critically failed for job %s: %s", job_id, exc)
        _mark_job_as_failed(job_id, str(exc))
    finally:
        # Гарантированное освобождение соединений с базой данных
        close_old_connections()


def dispatch_job(job_id: str) -> str:
    """Start job execution; returns the dispatch mode used.
    Raises RuntimeError if the local queue is full."""
    if settings.USE_CELERY and run_translation_task is not None:
        queue = getattr(settings, "PDFTRANSL_JOB_QUEUE", "pdftransl")
        run_translation_task.apply_async(args=[job_id], queue=queue)
        return "celery"
        
    # Защита от OOM из-за спама (только для локального режима)
    if _local_thread_pool._work_queue.qsize() >= MAX_QUEUE_SIZE:
        # Эту ошибку стоит ловить во views.py и возвращать клиенту HTTP 429
        raise RuntimeError("Server is too busy. Translation queue is full.")

    _local_thread_pool.submit(_run_job_in_thread, job_id)
    return "thread"