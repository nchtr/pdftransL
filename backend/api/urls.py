from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("jobs/", views.jobs, name="jobs"),
    path("jobs/<uuid:job_id>/", views.job_detail, name="job-detail"),
    path("jobs/<uuid:job_id>/events/", views.job_events, name="job-events"),
    path("jobs/<uuid:job_id>/segments/", views.job_segments, name="job-segments"),
    path(
        "jobs/<uuid:job_id>/segments/<int:order>/correct/",
        views.segment_correct,
        name="segment-correct",
    ),
    path("jobs/<uuid:job_id>/rebuild/", views.job_rebuild, name="job-rebuild"),
    path("jobs/<uuid:job_id>/download/", views.job_download, name="job-download"),
    path("providers/", views.providers, name="providers"),
    path("glossary/", views.glossary, name="glossary"),
    path("tm/stats/", views.tm_stats, name="tm-stats"),
    path("settings/", views.server_settings, name="settings"),
]
