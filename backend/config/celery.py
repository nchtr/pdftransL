import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("pdftransl")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
