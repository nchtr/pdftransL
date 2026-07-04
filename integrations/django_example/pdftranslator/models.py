import uuid

from django.db import models


class TranslationJob(models.Model):
    """Django-side record of a pdftransl job.

    The heavy state (QA report, TM) lives in the pdftransl engine; this
    model tracks the uploaded file and mirrors status for the UI/API.
    """

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETED = "completed"
        PARTIAL = "partial"          # finished, but some segments flagged
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pdf = models.FileField(upload_to="pdftransl/input/")
    source_lang = models.CharField(max_length=8, default="en")
    target_lang = models.CharField(max_length=8, default="ru")
    provider = models.CharField(max_length=64, blank=True, default="")
    model = models.CharField(max_length=128, blank=True, default="")

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    stage = models.CharField(max_length=32, blank=True, default="")
    progress = models.FloatField(default=0.0)

    output_markdown = models.CharField(max_length=512, blank=True, default="")
    assets_dir = models.CharField(max_length=512, blank=True, default="")
    report = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.id} [{self.status}]"
