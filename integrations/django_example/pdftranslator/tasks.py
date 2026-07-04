"""Celery task running the pdftransl pipeline for a Django job."""

import logging

from celery import shared_task
from django.conf import settings

from pdftransl.config import PipelineConfig
from pdftransl.pipeline import TranslationPipeline

from .models import TranslationJob

logger = logging.getLogger(__name__)


def _build_config(job: TranslationJob) -> PipelineConfig:
    overrides = {
        "source_lang": job.source_lang,
        "target_lang": job.target_lang,
        "db_path": str(getattr(settings, "PDFTRANSL_DB", "data/pdftransl.db")),
        "output_dir": str(getattr(settings, "PDFTRANSL_OUTPUT_DIR", "data/output")),
    }
    if job.provider:
        overrides["provider"] = job.provider
    if job.model:
        overrides["model"] = job.model
    return PipelineConfig.from_env(**overrides)


@shared_task(bind=True, max_retries=1)
def run_translation(self, job_pk: str) -> str:
    job = TranslationJob.objects.get(pk=job_pk)
    job.status = TranslationJob.Status.RUNNING
    job.save(update_fields=["status", "updated_at"])

    def on_stage(stage: str, progress: float) -> None:
        TranslationJob.objects.filter(pk=job_pk).update(
            stage=stage, progress=progress
        )

    try:
        pipeline = TranslationPipeline(_build_config(job))
        result = pipeline.run(job.pdf.path, on_stage=on_stage, job_id=str(job.pk))
    except Exception as exc:
        logger.exception("Translation job %s crashed", job_pk)
        job.status = TranslationJob.Status.FAILED
        job.error = str(exc)
        job.save(update_fields=["status", "error", "updated_at"])
        raise

    job.status = result.status
    job.output_markdown = result.output_markdown_path or ""
    job.assets_dir = result.assets_dir or ""
    job.report = result.report
    job.error = result.error or ""
    job.progress = 1.0
    job.save()
    return job.status
