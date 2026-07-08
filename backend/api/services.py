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
from pdftransl.rag.glossary import Glossary
from pdftransl.rag.store import TranslationMemory
from pdftransl.service import looks_like_term

from .models import SegmentRecord, ServerConfig, TranslationJob

logger = logging.getLogger(__name__)

# option keys shared by per-job options AND runtime server settings
_BOOL_KEYS = (
    "review", "use_rag", "learn", "bilingual", "describe_figures",
    "backtranslation_check", "doc_summary", "auto_glossary",
    "skip_references", "quality_score", "fix_latex", "render_check",
    "structured_outputs", "ocr_on_scan", "parser_fallback",
    "adaptive_throttle", "parse_cache", "resume",
)
_BOOL_KEYS = _BOOL_KEYS + ("memory_guard",)
_STR_KEYS = (
    "provider", "model", "vision_provider", "vision_model",
    "parser_backend", "domain", "source_lang", "target_lang",
    "ocr_prompt",
)
_INT_KEYS = (
    "max_workers", "rpm_limit", "parser_timeout", "ocr_dpi",
    "tm_autoexport_every", "min_free_memory_mb", "memory_wait_timeout",
    "stall_warning_seconds", "max_ocr_pages",
)


def _apply_options(overrides: dict, options: dict) -> None:
    """Map an options dict (job options or runtime settings) onto
    PipelineConfig overrides. Unknown keys are ignored."""
    for key in _BOOL_KEYS:
        if key in options and options[key] is not None:
            overrides[key] = bool(options[key])
    for key in _STR_KEYS:
        if options.get(key):
            target = "tm_domain" if key == "domain" else key
            overrides[target] = str(options[key])
    for key in _INT_KEYS:
        if options.get(key):
            overrides[key] = int(options[key])
    if options.get("formats"):
        overrides["export_formats"] = list(options["formats"])
    if options.get("fallback_providers"):
        value = options["fallback_providers"]
        if isinstance(value, str):
            value = [p.strip() for p in value.split(",") if p.strip()]
        overrides["fallback_providers"] = list(value)


def runtime_settings() -> dict:
    """Server-wide defaults saved from the web UI (may be empty)."""
    try:
        return dict(ServerConfig.load().data or {})
    except Exception:  # e.g. before migrations have run
        return {}


def scan_upload(uploaded_file) -> tuple[bool, str]:
    """Optional antivirus / content scan of an uploaded file.

    Runs the command in ``PDFTRANSL_AV_SCAN_CMD`` (e.g. ``clamscan``)
    against a temp copy; a non-zero exit rejects the upload. When the
    env var is unset, uploads pass through. Returns ``(ok, reason)``.
    """
    import os
    import shlex
    import subprocess
    import tempfile

    cmd = os.environ.get("PDFTRANSL_AV_SCAN_CMD", "").strip()
    if not cmd:
        return True, ""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        for chunk in uploaded_file.chunks():
            tmp.write(chunk)
        tmp.flush()
        uploaded_file.seek(0)
        try:
            proc = subprocess.run(
                shlex.split(cmd) + [tmp.name],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("AV scan could not run: %s", exc)
            return False, "virus scan unavailable"
    if proc.returncode != 0:
        logger.warning("AV scan rejected upload: %s", (proc.stdout or "")[-200:])
        return False, "failed virus scan"
    return True, ""


def build_config(job: TranslationJob) -> PipelineConfig:
    overrides: dict = {
        "db_path": settings.PDFTRANSL_DB,
        "output_dir": settings.PDFTRANSL_OUTPUT_DIR,
    }
    # precedence: env defaults < runtime server settings < per-job options
    _apply_options(overrides, runtime_settings())
    overrides["source_lang"] = job.source_lang
    overrides["target_lang"] = job.target_lang
    if job.provider:
        overrides["provider"] = job.provider
    if job.model:
        overrides["model"] = job.model
    _apply_options(overrides, job.options or {})
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

    def should_pause() -> bool:
        # Cheap poll (once per finished segment) of the flag the /pause/
        # endpoint sets; translation stops cooperatively once it sees True.
        return TranslationJob.objects.filter(
            pk=job_id, pause_requested=True
        ).exists()

    try:
        config = build_config(job)
        pipeline = TranslationPipeline(config)
        result = pipeline.run(
            job.pdf.path, on_stage=on_stage, job_id=str(job.pk),
            should_pause=should_pause,
        )
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
    job.pause_requested = False  # request fulfilled (or moot for any other outcome)
    job.outputs = outputs
    job.assets_dir = result.assets_dir or ""
    job.report = result.report
    job.error = result.error or ""
    if result.status in ("completed", "partial"):
        job.progress = 1.0
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


def pause_job(job: TranslationJob) -> None:
    """Ask a queued/running job to stop after its current segment(s).

    The worker (``run_job``, via the pipeline's ``should_pause`` poll)
    checks this between segments and flips the job to PAUSED once it has
    actually stopped — this just raises the flag.
    """
    job.pause_requested = True
    job.save(update_fields=["pause_requested", "updated_at"])


def prepare_resume(job: TranslationJob) -> None:
    """Reset a PAUSED job so it can be re-dispatched.

    Nothing is re-translated from scratch: the per-document checkpoint
    written during the paused run already holds every finished segment,
    and the pipeline's ``resume`` option (on by default) reuses it.
    """
    job.status = TranslationJob.Status.QUEUED
    job.pause_requested = False
    job.error = ""
    job.save(update_fields=["status", "pause_requested", "error", "updated_at"])


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
    # term-sized corrections also grow the glossary
    if looks_like_term(segment.source_text) and looks_like_term(segment.corrected):
        Glossary(config.db_path).add(
            segment.source_text.strip(), segment.corrected.strip(),
            job.source_lang, job.target_lang,
            notes=f"from correction in job {job.pk}",
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
    formats = [f for f in config.export_formats if f in ("html", "docx", "pdf", "latex")]
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
