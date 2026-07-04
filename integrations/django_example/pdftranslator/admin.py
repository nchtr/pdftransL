from django.contrib import admin

from .models import TranslationJob


@admin.register(TranslationJob)
class TranslationJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "stage", "progress", "target_lang", "created_at")
    list_filter = ("status", "target_lang")
    readonly_fields = ("report", "created_at", "updated_at")
