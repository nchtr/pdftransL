"""Minimal JSON API: upload PDF -> poll status -> download result.

Uses plain Django (no DRF) to keep the example dependency-free.
Add authentication/permissions before production use.
"""

import json
from pathlib import Path

from django.http import FileResponse, Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import TranslationJob
from .tasks import run_translation


@csrf_exempt
@require_POST
def upload(request):
    """POST multipart: file=<pdf> [source_lang, target_lang, provider, model]."""
    pdf = request.FILES.get("file")
    if pdf is None:
        return JsonResponse({"error": "no file"}, status=400)
    if not pdf.name.lower().endswith(".pdf"):
        return JsonResponse({"error": "only PDF files are accepted"}, status=400)

    job = TranslationJob.objects.create(
        pdf=pdf,
        source_lang=request.POST.get("source_lang", "en"),
        target_lang=request.POST.get("target_lang", "ru"),
        provider=request.POST.get("provider", ""),
        model=request.POST.get("model", ""),
    )
    run_translation.delay(str(job.pk))
    return JsonResponse({"job_id": str(job.pk), "status": job.status}, status=202)


@require_GET
def status(request, job_id):
    try:
        job = TranslationJob.objects.get(pk=job_id)
    except TranslationJob.DoesNotExist:
        raise Http404
    return JsonResponse({
        "job_id": str(job.pk),
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "error": job.error or None,
        "report": job.report,
    })


@require_GET
def download(request, job_id):
    try:
        job = TranslationJob.objects.get(pk=job_id)
    except TranslationJob.DoesNotExist:
        raise Http404
    if job.status not in (TranslationJob.Status.COMPLETED, TranslationJob.Status.PARTIAL):
        return JsonResponse({"error": f"job is {job.status}"}, status=409)
    path = Path(job.output_markdown)
    if not path.exists():
        raise Http404
    return FileResponse(
        open(path, "rb"), as_attachment=True, filename=path.name,
        content_type="text/markdown",
    )


@csrf_exempt
@require_POST
def correction(request):
    """POST JSON {source, corrected} — human feedback into the TM."""
    from pdftransl.service import TranslationService

    try:
        body = json.loads(request.body)
        source, corrected = body["source"], body["corrected"]
    except (ValueError, KeyError):
        return JsonResponse({"error": "expected JSON {source, corrected}"}, status=400)
    TranslationService().add_correction(source, corrected)
    return JsonResponse({"ok": True})
