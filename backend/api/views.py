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
from .models import SegmentRecord, TranslationJob
from .tasks import dispatch_job

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


@require_GET
def job_detail(request, job_id):
    return JsonResponse(_job_or_404(job_id).as_dict())


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
@require_http_methods(["GET", "POST"])
def glossary(request):
    config = _engine_config()
    store = Glossary(config.db_path)
    if request.method == "GET":
        return JsonResponse({"terms": store.list_all()})
    try:
        body = json.loads(request.body)
        term, translation = body["term"], body["translation"]
    except (ValueError, KeyError):
        return JsonResponse({"error": "expected JSON {term, translation}"}, status=400)
    store.add(
        term, translation,
        body.get("source_lang", config.source_lang),
        body.get("target_lang", config.target_lang),
        body.get("notes"),
    )
    return JsonResponse({"ok": True}, status=201)


@require_GET
def tm_stats(request):
    config = _engine_config()
    tm = TranslationMemory(config.db_path, get_embedder(config))
    return JsonResponse(tm.stats())


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
