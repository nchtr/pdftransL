from django.urls import path

from . import views

app_name = "pdftranslator"

urlpatterns = [
    path("jobs/", views.upload, name="upload"),
    path("jobs/<uuid:job_id>/", views.status, name="status"),
    path("jobs/<uuid:job_id>/download/", views.download, name="download"),
    path("corrections/", views.correction, name="correction"),
]
