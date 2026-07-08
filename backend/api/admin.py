"""Регистрация моделей в Django-админке (просмотр задач/сегментов)."""

from django.contrib import admin

from .models import SegmentRecord, TranslationJob


@admin.register(TranslationJob)
class TranslationJobAdmin(admin.ModelAdmin):
    list_display = ("id", "original_name", "status", "stage", "progress",
                    "target_lang", "created_at")
    list_filter = ("status", "target_lang", "provider")
    readonly_fields = ("report", "outputs", "created_at", "updated_at")
    search_fields = ("original_name",)


@admin.register(SegmentRecord)
class SegmentRecordAdmin(admin.ModelAdmin):
    list_display = ("job", "order", "kind", "ok")
    list_filter = ("kind", "ok")
