"""Django settings for the pdftransl backend.

Everything deployment-specific comes from environment variables so the
same code runs in dev (sqlite, thread workers) and prod (postgres,
celery + redis) without edits.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
REPO_ROOT = BASE_DIR.parent                                # repo root
DATA_DIR = Path(os.environ.get("PDFTRANSL_DATA_DIR", REPO_ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "media").mkdir(exist_ok=True)

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dev-only-secret-key-change-in-production"
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1").strip().lower() in ("1", "true", "yes")
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "api.middleware.cors_middleware",
    "api.middleware.token_auth_middleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [REPO_ROOT / "frontend" / "dist"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", DATA_DIR / "django.sqlite3"),
    }
}
# For PostgreSQL set DATABASE_URL-style envs and swap the engine here.

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "ru"
TIME_ZONE = os.environ.get("TZ", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = DATA_DIR / "static"
# built React SPA (frontend/dist) is served by the catch-all view
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
STATICFILES_DIRS = [FRONTEND_DIST] if FRONTEND_DIST.exists() else []

MEDIA_URL = "/media/"
MEDIA_ROOT = DATA_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- pdftransl -----------------------------------------------------------
PDFTRANSL_DB = str(DATA_DIR / "pdftransl.db")
PDFTRANSL_OUTPUT_DIR = str(DATA_DIR / "output")
MAX_UPLOAD_MB = int(os.environ.get("PDFTRANSL_MAX_UPLOAD_MB", "50"))
# optional bearer token protecting /api/ (empty = open, dev mode)
PDFTRANSL_API_TOKEN = os.environ.get("PDFTRANSL_API_TOKEN", "")
# per-IP upload throttle (uploads per hour)
UPLOADS_PER_HOUR = int(os.environ.get("PDFTRANSL_UPLOADS_PER_HOUR", "20"))

# --- task execution -------------------------------------------------------
# USE_CELERY=1 -> celery worker (redis broker); default: background threads
USE_CELERY = os.environ.get("USE_CELERY", "0").strip().lower() in ("1", "true", "yes")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_TIME_LIMIT = 3600

# --- CORS (React dev server) ----------------------------------------------
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]
