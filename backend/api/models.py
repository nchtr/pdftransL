import uuid

from django.db import models


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
            "formats": [f for f, p in (self.outputs or {}).items() if p],
            "report": self.report,
            "error": self.error or None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


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
