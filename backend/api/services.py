"""Bridge between Django models and the pdftransl engine."""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

from pdftransl.config import PipelineConfig
from pdftransl.export.exporter import export_document
from pdftransl.parsing.splitter import assemble
from pdftransl.pipeline import TranslationPipeline
from pdftransl.rag.embeddings import get_embedder
from pdftransl.rag.store import TranslationMemory

from .models import SegmentRecord, TranslationJob

logger = logging.getLogger(__name__)


def build_config(job: TranslationJob) -> PipelineConfig:
    options = job.options or {}
    overrides: dict = {
        "source_lang": job.source_lang,
        "target_lang": job.target_lang,
        "db_path": settings.PDFTRANSL_DB,
        "output_dir": settings.PDFTRANSL_OUTPUT_DIR,
    }
    if job.provider:
        overrides["provider"] = job.provider
    if job.model:
        overrides["model"] = job.model
    for key in (
        "review", "use_rag", "learn", "bilingual", "describe_figures",
        "backtranslation_check", "doc_summary", "auto_glossary",
        "skip_references",
    ):
        if key in options:
            overrides[key] = bool(options[key])
    if options.get("formats"):
        overrides["export_formats"] = list(options["formats"])
    if options.get("max_workers"):
        overrides["max_workers"] = int(options["max_workers"])
    if options.get("fallback_providers"):
        overrides["fallback_providers"] = list(options["fallback_providers"])
    if options.get("domain"):
        overrides["tm_domain"] = str(options["domain"])
    return PipelineConfig.from_env(**overrides)


def run_job(job_id: str) -> str:
    """Execute the pipeline for a job; called from Celery or a thread."""
    job = TranslationJob.objects.get(pk=job_id)
    job.status = TranslationJob.Status.RUNNING
    job.save(update_fields=["status", "updated_at"])

    def on_stage(stage: str, progress: float) -> None:
        TranslationJob.objects.filter(pk=job_id).update(
            stage=stage, progress=round(progress, 3)
        )

    try:
        config = build_config(job)
        pipeline = TranslationPipeline(config)
        result = pipeline.run(job.pdf.path, on_stage=on_stage, job_id=str(job.pk))
    except Exception as exc:  # noqa: BLE001 - job must record any failure
        logger.exception("Job %s crashed", job_id)
        job.status = TranslationJob.Status.FAILED
        job.error = str(exc)
        job.save(update_fields=["status", "error", "updated_at"])
        return job.status

    outputs = {}
    if result.output_markdown_path:
        outputs["md"] = result.output_markdown_path
    for fmt, path in (result.exports or {}).items():
        if path:
            outputs[fmt] = path
    if result.report.get("bilingual_markdown"):
        outputs["bilingual"] = result.report["bilingual_markdown"]
    if result.report_path:
        outputs["report"] = result.report_path

    job.status = result.status
    job.outputs = outputs
    job.assets_dir = result.assets_dir or ""
    job.report = result.report
    job.error = result.error or ""
    job.progress = 1.0 if result.status != "failed" else job.progress
    job.save()

    SegmentRecord.objects.filter(job=job).delete()
    SegmentRecord.objects.bulk_create([
        SegmentRecord(
            job=job,
            order=i,
            kind=seg["kind"],
            source_text=seg["source_text"],
            translation=seg.get("translation") or "",
            ok=seg.get("ok", True),
            issues=seg.get("issues", []),
        )
        for i, seg in enumerate(result.segments)
    ])
    return job.status


def save_correction(job: TranslationJob, order: int, corrected: str) -> SegmentRecord:
    """Store a human correction and feed it into the translation memory."""
    segment = SegmentRecord.objects.get(job=job, order=order)
    segment.corrected = corrected.strip()
    segment.ok = True
    segment.save(update_fields=["corrected", "ok"])

    config = build_config(job)
    tm = TranslationMemory(config.db_path, get_embedder(config))
    tm.add(
        segment.source_text, segment.corrected,
        job.source_lang, job.target_lang,
        origin="human", doc_id=str(job.pk),
        domain=(job.options or {}).get("domain", ""),
    )
    return segment


def rebuild_outputs(job: TranslationJob) -> dict:
    """Reassemble the document from (corrected) segments and re-export."""
    segments = list(job.segments.all())
    if not segments:
        raise ValueError("job has no stored segments")
    markdown = assemble([s.final_text() for s in segments])

    md_path = Path(job.outputs.get("md") or "")
    if not md_path.parent.exists():
        raise ValueError("original output directory is gone")
    md_path.write_text(markdown, encoding="utf-8")

    config = build_config(job)
    formats = [f for f in config.export_formats if f in ("html", "docx", "pdf")]
    export_result = export_document(
        markdown,
        out_base=md_path.with_suffix(""),
        formats=formats,
        assets_dir=job.assets_dir or None,
        title=job.original_name or md_path.stem,
    )
    outputs = dict(job.outputs)
    for fmt, path in export_result["files"].items():
        if path:
            outputs[fmt] = path
    job.outputs = outputs
    job.save(update_fields=["outputs", "updated_at"])
    return {"outputs": outputs, "engines": export_result["engines"]}
