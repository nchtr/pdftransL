import uuid

from django.db import models
from django.utils import timezone

from pdftransl.progress import estimate_eta_seconds


class ServerConfig(models.Model):
    """Runtime server defaults, editable from the web UI without a
    restart. Stored as one JSON blob (option-name -> value) using the
    same keys as per-job options; per-job options still win."""

    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "server configuration"

    @classmethod
    def load(cls) -> "ServerConfig":
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self) -> str:
        return f"server config ({len(self.data or {})} overrides)"


class TranslationJob(models.Model):
    """A PDF translation job and its produced artifacts."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETED = "completed"
        PARTIAL = "partial"          # finished, but some segments flagged
        FAILED = "failed"
        PAUSED = "paused"            # stopped on request; resumable from checkpoint

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pdf = models.FileField(upload_to="input/%Y/%m/")
    original_name = models.CharField(max_length=255, blank=True, default="")

    source_lang = models.CharField(max_length=8, default="en")
    target_lang = models.CharField(max_length=8, default="ru")
    provider = models.CharField(max_length=64, blank=True, default="")
    model = models.CharField(max_length=128, blank=True, default="")
    # pipeline options chosen in the UI: formats, review, rag, bilingual,
    # describe_figures, backtranslation, max_workers, fallback...
    options = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    # Set by the /pause/ endpoint while a job is queued or running; the
    # worker polls it between segments and stops cooperatively, flipping
    # status to PAUSED once it actually has. Cleared on resume.
    pause_requested = models.BooleanField(default=False)
    stage = models.CharField(max_length=32, blank=True, default="")
    progress = models.FloatField(default=0.0)
    # [{key, label, weight, start}, ...] from pdftransl.progress.build_stage_plan,
    # computed once at creation time from this job's actual config — lets the UI
    # render a per-stage breakdown instead of one flat percentage.
    stage_plan = models.JSONField(default=list, blank=True)
    # Set when the worker actually starts running (not at creation/queue
    # time) and reset on every re-dispatch (incl. resume) — the reference
    # point for the ETA estimate, so time spent paused/queued isn't
    # mistaken for slow progress.
    started_at = models.DateTimeField(null=True, blank=True)

    # format -> absolute path: md / html / docx / pdf / bilingual / report
    outputs = models.JSONField(default=dict, blank=True)
    assets_dir = models.CharField(max_length=512, blank=True, default="")
    report = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.original_name or self.id} [{self.status}]"

    def eta_seconds(self) -> "float | None":
        """Approximate seconds remaining, extrapolated from how long this
        run has taken to reach its current progress. Only meaningful
        while actually running (queued/paused/finished jobs have no
        "remaining" to speak of)."""
        if self.status != self.Status.RUNNING or not self.started_at:
            return None
        elapsed = (timezone.now() - self.started_at).total_seconds()
        return estimate_eta_seconds(elapsed, self.progress)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.original_name,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "provider": self.provider,
            "model": self.model,
            "options": self.options,
            "status": self.status,
            "pause_requested": self.pause_requested,
            "stage": self.stage,
            "progress": self.progress,
            "stage_plan": self.stage_plan,
            "eta_seconds": self.eta_seconds(),
            "formats": [f for f, p in (self.outputs or {}).items() if p],
            "report": self.report,
            "error": self.error or None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def as_list_dict(self) -> dict:
        """Lean payload for the job-list SSE stream: no ``report`` (can be
        sizeable — LaTeX issues, segment previews...) since the list view
        doesn't render it; JobDetail fetches the full record separately."""
        d = self.as_dict()
        d.pop("report", None)
        return d


class SegmentRecord(models.Model):
    """Per-segment source/translation pair for the review UI."""

    job = models.ForeignKey(
        TranslationJob, on_delete=models.CASCADE, related_name="segments"
    )
    order = models.PositiveIntegerField()
    kind = models.CharField(max_length=16)          # translate | pass
    source_text = models.TextField()
    translation = models.TextField(blank=True, default="")
    corrected = models.TextField(blank=True, default="")   # human edit
    ok = models.BooleanField(default=True)
    issues = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["order"]
        unique_together = [("job", "order")]

    def final_text(self) -> str:
        if self.kind != "translate":
            return self.source_text
        return self.corrected or self.translation or self.source_text

    def as_dict(self) -> dict:
        return {
            "order": self.order,
            "kind": self.kind,
            "source_text": self.source_text,
            "translation": self.translation,
            "corrected": self.corrected or None,
            "ok": self.ok,
            "issues": self.issues,
        }
