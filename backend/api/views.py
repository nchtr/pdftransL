"""JSON API consumed by the React SPA and the Telegram bot.

NOTE: endpoints are unauthenticated by design of this starter — add
auth (session/token) before exposing publicly.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from pathlib import Path

from django.conf import settings
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    JsonResponse,
    StreamingHttpResponse,
)
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from pdftransl.config import PROVIDER_PRESETS, PipelineConfig
from pdftransl.export.exporter import available_engines
from pdftransl.rag.embeddings import get_embedder
from pdftransl.rag.glossary import Glossary
from pdftransl.rag.store import TranslationMemory
from pdftransl.translation.prompts import LANG_NAMES

from . import services
from .models import SegmentRecord, ServerConfig, TranslationJob
from .tasks import dispatch_job

# keys the web UI may store as runtime server defaults (value type for
# light validation; None value in a PUT removes the override)
_SETTING_TYPES: dict[str, type] = {
    "provider": str, "model": str, "vision_provider": str, "vision_model": str,
    "source_lang": str, "target_lang": str, "parser_backend": str,
    "domain": str, "fallback_providers": list, "formats": list,
    "max_workers": int, "rpm_limit": int, "parser_timeout": int, "ocr_dpi": int,
    "review": bool, "use_rag": bool, "learn": bool, "bilingual": bool,
    "describe_figures": bool, "backtranslation_check": bool,
    "doc_summary": bool, "auto_glossary": bool, "skip_references": bool,
    "quality_score": bool, "fix_latex": bool, "render_check": bool,
    "structured_outputs": bool, "ocr_on_scan": bool, "parser_fallback": bool,
    "adaptive_throttle": bool, "parse_cache": bool, "resume": bool,
    "memory_guard": bool,
    "tm_autoexport_every": int, "min_free_memory_mb": int,
    "memory_wait_timeout": int, "stall_warning_seconds": int, "max_ocr_pages": int,
    "ocr_dpi": int, "ocr_prompt": str,
    "log_level": str,
}

_CONTENT_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "bilingual": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "latex": "application/x-tex; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "report": "application/json",
}


def _job_or_404(job_id) -> TranslationJob:
    try:
        return TranslationJob.objects.get(pk=job_id)
    except (TranslationJob.DoesNotExist, ValueError):
        raise Http404


def _engine_config() -> PipelineConfig:
    return PipelineConfig.from_env(
        db_path=settings.PDFTRANSL_DB, output_dir=settings.PDFTRANSL_OUTPUT_DIR
    )


# --- jobs -------------------------------------------------------------

# per-IP upload timestamps for the throttle (in-memory: per process)
_upload_log: dict[str, deque] = defaultdict(deque)


def _upload_allowed(ip: str) -> bool:
    now = time.time()
    log = _upload_log[ip]
    while log and now - log[0] > 3600:
        log.popleft()
    if len(log) >= settings.UPLOADS_PER_HOUR:
        return False
    log.append(now)
    return True


@csrf_exempt
@require_http_methods(["GET", "POST"])
def jobs(request):
    if request.method == "GET":
        items = [j.as_dict() for j in TranslationJob.objects.all()[:100]]
        return JsonResponse({"jobs": items})

    ip = request.META.get("REMOTE_ADDR", "?")
    if not _upload_allowed(ip):
        return JsonResponse(
            {"error": f"upload limit reached ({settings.UPLOADS_PER_HOUR}/hour); "
                      "try again later"},
            status=429,
        )

    pdf = request.FILES.get("file")
    if pdf is None:
        return JsonResponse({"error": "no file"}, status=400)
    if not pdf.name.lower().endswith(".pdf"):
        return JsonResponse({"error": "only PDF files are accepted"}, status=400)
    if pdf.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        return JsonResponse(
            {"error": f"file larger than {settings.MAX_UPLOAD_MB} MB"}, status=413
        )
    # validate the actual content, not just the extension
    head = pdf.read(5)
    pdf.seek(0)
    if head[:5] != b"%PDF-":
        return JsonResponse(
            {"error": "file does not look like a PDF (bad signature)"}, status=400
        )
    # optional antivirus / content scan hook (PDFTRANSL_AV_SCAN_CMD)
    ok, reason = services.scan_upload(pdf)
    if not ok:
        return JsonResponse({"error": f"upload rejected: {reason}"}, status=422)

    options: dict = {}
    raw_options = request.POST.get("options")
    if raw_options:
        try:
            options = json.loads(raw_options)
        except ValueError:
            return JsonResponse({"error": "options must be JSON"}, status=400)

    job = TranslationJob.objects.create(
        pdf=pdf,
        original_name=pdf.name,
        source_lang=request.POST.get("source_lang", "en"),
        target_lang=request.POST.get("target_lang", "ru"),
        provider=request.POST.get("provider", ""),
        model=request.POST.get("model", ""),
        options=options,
    )
    mode = dispatch_job(str(job.pk))
    return JsonResponse(
        {"job_id": str(job.pk), "status": job.status, "dispatch": mode}, status=202
    )


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
def job_detail(request, job_id):
    job = _job_or_404(job_id)
    if request.method == "GET":
        return JsonResponse(job.as_dict())

    # DELETE: remove the job row, its uploaded PDF and its output dir
    if job.status == TranslationJob.Status.RUNNING:
        return JsonResponse({"error": "job is running; wait for it to finish"},
                            status=409)
    import shutil

    output_root = Path(settings.PDFTRANSL_OUTPUT_DIR).resolve()
    md = (job.outputs or {}).get("md")
    if md:
        out_dir = Path(md).resolve().parent
        # never delete outside the configured output root
        if out_dir.exists() and str(out_dir).startswith(str(output_root)):
            shutil.rmtree(out_dir, ignore_errors=True)
    try:
        job.pdf.delete(save=False)
    except Exception:
        pass
    job.delete()
    return JsonResponse({"deleted": True})


@require_GET
def job_events(request, job_id):
    """Server-Sent Events stream of job progress.

    Emits a `data:` line whenever status/stage/progress changes and
    closes on a terminal status. Frontends may use EventSource here
    instead of polling; polling keeps working either way.
    """
    _job_or_404(job_id)  # 404 early, before we start streaming
    terminal = {"completed", "partial", "failed"}

    def stream():
        last = None
        for _ in range(1800):  # safety cap: ~30 min
            try:
                job = TranslationJob.objects.get(pk=job_id)
            except TranslationJob.DoesNotExist:
                break
            payload = json.dumps({
                "status": job.status,
                "stage": job.stage,
                "progress": job.progress,
            })
            if payload != last:
                last = payload
                yield f"data: {payload}\n\n"
            if job.status in terminal:
                break
            time.sleep(1)

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_GET
def job_segments(request, job_id):
    job = _job_or_404(job_id)
    only_flagged = request.GET.get("flagged") == "1"
    queryset = job.segments.all()
    if only_flagged:
        queryset = queryset.filter(ok=False)
    try:
        offset = max(0, int(request.GET.get("offset", 0)))
        limit = min(200, int(request.GET.get("limit", 50)))
    except ValueError:
        return JsonResponse({"error": "bad pagination params"}, status=400)
    total = queryset.count()
    items = [s.as_dict() for s in queryset[offset:offset + limit]]
    return JsonResponse({"total": total, "segments": items})


@csrf_exempt
@require_POST
def segment_correct(request, job_id, order):
    job = _job_or_404(job_id)
    try:
        body = json.loads(request.body)
        corrected = body["corrected"]
    except (ValueError, KeyError):
        return JsonResponse({"error": "expected JSON {corrected}"}, status=400)
    try:
        segment = services.save_correction(job, int(order), corrected)
    except SegmentRecord.DoesNotExist:
        raise Http404
    return JsonResponse(segment.as_dict())


@csrf_exempt
@require_POST
def job_rebuild(request, job_id):
    job = _job_or_404(job_id)
    try:
        result = services.rebuild_outputs(job)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse(result)


@require_GET
def job_download(request, job_id):
    job = _job_or_404(job_id)
    fmt = request.GET.get("format", "md")
    path = (job.outputs or {}).get(fmt)
    if not path:
        return JsonResponse(
            {"error": f"format '{fmt}' not available", "have": list(job.outputs or {})},
            status=404,
        )
    file_path = Path(path)
    if not file_path.exists():
        raise Http404
    return FileResponse(
        open(file_path, "rb"),
        as_attachment=fmt not in ("html",),
        filename=file_path.name,
        content_type=_CONTENT_TYPES.get(fmt, "application/octet-stream"),
    )


# --- meta / options -----------------------------------------------------


@require_GET
def providers(request):
    presets = [
        {
            "name": p.name,
            "model": p.model,
            "is_local": p.is_local,
            "supports_vision": p.supports_vision,
            "needs_api_key": bool(p.api_key_env),
            "key_configured": bool(p.resolve_api_key()) or p.is_local,
        }
        for p in PROVIDER_PRESETS.values()
    ]
    return JsonResponse({
        "providers": presets,
        "languages": LANG_NAMES,
        "export_engines": available_engines(),
    })


# --- glossary / TM --------------------------------------------------------


@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE"])
def glossary(request):
    config = _engine_config()
    store = Glossary(config.db_path)
    if request.method == "GET":
        return JsonResponse({"terms": store.list_all()})
    try:
        body = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "expected a JSON body"}, status=400)
    src = body.get("source_lang", config.source_lang)
    tgt = body.get("target_lang", config.target_lang)
    if request.method == "DELETE":
        term = body.get("term")
        if not term:
            return JsonResponse({"error": "expected JSON {term}"}, status=400)
        removed = store.remove(term, src, tgt)
        return JsonResponse({"removed": removed})
    try:
        term, translation = body["term"], body["translation"]
    except KeyError:
        return JsonResponse({"error": "expected JSON {term, translation}"}, status=400)
    store.add(term, translation, src, tgt, body.get("notes"))
    return JsonResponse({"ok": True}, status=201)


@require_GET
def tm_stats(request):
    config = _engine_config()
    tm = TranslationMemory(config.db_path, get_embedder(config))
    return JsonResponse(tm.stats())


# --- runtime server settings ------------------------------------------------


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def server_settings(request):
    """Runtime defaults applied to every new job — editable from the web
    UI with no server restart. Per-job options still override these."""
    config_row = ServerConfig.load()

    if request.method == "GET":
        defaults = PipelineConfig.from_env()
        return JsonResponse({
            "settings": config_row.data or {},
            "defaults": {
                "provider": defaults.provider,
                "model": defaults.model or "",
                "vision_model": defaults.vision_model or "",
                "parser_backend": defaults.parser_backend,
                "parser_timeout": defaults.parser_timeout,
                "max_workers": defaults.max_workers,
                "rpm_limit": defaults.rpm_limit or 0,
                "formats": defaults.export_formats,
                "ocr_on_scan": defaults.ocr_on_scan,
                "log_level": settings.PDFTRANSL_LOG_LEVEL,
            },
            "editable": sorted(_SETTING_TYPES),
        })

    try:
        body = json.loads(request.body)
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "expected a JSON object"}, status=400)

    data = dict(config_row.data or {})
    errors = {}
    for key, value in body.items():
        if key not in _SETTING_TYPES:
            errors[key] = "unknown setting"
            continue
        if value is None or value == "":
            data.pop(key, None)      # null/empty removes the override
            continue
        expected = _SETTING_TYPES[key]
        try:
            if expected is bool:
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            elif expected is int:
                value = int(value)
            elif expected is list:
                if isinstance(value, str):
                    value = [v.strip() for v in value.split(",") if v.strip()]
                value = list(value)
            else:
                value = str(value)
        except (TypeError, ValueError):
            errors[key] = f"expected {expected.__name__}"
            continue
        data[key] = value
    if errors:
        return JsonResponse({"error": "invalid settings", "details": errors}, status=400)

    # log level applies immediately, process-wide
    if "log_level" in data:
        from pdftransl.logging_setup import set_level

        try:
            data["log_level"] = set_level(data["log_level"])
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    config_row.data = data
    config_row.save()
    return JsonResponse({"settings": data})


# --- SPA ---------------------------------------------------------------------


def spa_index(request):
    index = settings.FRONTEND_DIST / "index.html"
    if index.exists():
        return HttpResponse(index.read_text(encoding="utf-8"))
    return HttpResponse(
        "<h1>pdftransl</h1><p>React UI is not built. Run "
        "<code>cd frontend && npm install && npm run build</code> "
        "or use the API under <code>/api/</code>.</p>",
        content_type="text/html",
    )
