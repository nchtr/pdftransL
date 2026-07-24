"""Настройки Django-бэкенда pdftransl.

Всё деплой-специфичное берётся из переменных окружения, поэтому один и
тот же код работает в dev (sqlite, фоновые потоки) и в проде (postgres,
celery + redis) без правок.
"""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
REPO_ROOT = BASE_DIR.parent                                # repo root

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:  # pragma: no cover - dependency is installed in supported setups
    pass

DATA_DIR = Path(os.environ.get("PDFTRANSL_DATA_DIR", REPO_ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "media").mkdir(exist_ok=True)

DEBUG = os.environ.get("DJANGO_DEBUG", "0").strip().lower() in ("1", "true", "yes")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        # A new key is adequate for an explicitly local development process;
        # production must use a stable secret supplied by the deployer.
        SECRET_KEY = secrets.token_urlsafe(50)
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is disabled")
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
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

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
REFERRER_POLICY = "same-origin"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "0").strip().lower() in ("1", "true", "yes")
SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT

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
        "OPTIONS": {"timeout": 30},
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
# Optional multi-user token map: ``owner:secret,other:secret``.  The legacy
# single token remains owner ``default`` for backward compatibility.
PDFTRANSL_API_TOKENS = os.environ.get("PDFTRANSL_API_TOKENS", "")
PDFTRANSL_ADMIN_OWNERS = {
    value.strip() for value in os.environ.get("PDFTRANSL_ADMIN_OWNERS", "default").split(",")
    if value.strip()
}
if not DEBUG and not (PDFTRANSL_API_TOKEN or PDFTRANSL_API_TOKENS):
    raise RuntimeError("PDFTRANSL_API_TOKEN or PDFTRANSL_API_TOKENS must be set when DJANGO_DEBUG is disabled")
# per-IP upload throttle (uploads per hour)
UPLOADS_PER_HOUR = int(os.environ.get("PDFTRANSL_UPLOADS_PER_HOUR", "20"))

# --- task execution -------------------------------------------------------
# USE_CELERY=1 -> celery worker (redis broker); default: background threads
USE_CELERY = os.environ.get("USE_CELERY", "0").strip().lower() in ("1", "true", "yes")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_TIME_LIMIT = int(os.environ.get("CELERY_TASK_TIME_LIMIT", "7800"))
CELERY_TASK_SOFT_TIME_LIMIT = int(os.environ.get("CELERY_TASK_SOFT_TIME_LIMIT", "7500"))
# queue new jobs land on; point a GPU worker at a heavy queue if desired
PDFTRANSL_JOB_QUEUE = os.environ.get("PDFTRANSL_JOB_QUEUE", "pdftransl")
CELERY_TASK_DEFAULT_QUEUE = "pdftransl"

# --- CORS (React dev server) ----------------------------------------------
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]

if os.environ.get("DJANGO_CACHE_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.environ["DJANGO_CACHE_URL"],
        }
    }

# --- logging ----------------------------------------------------------------
# PDFTRANSL_LOG_LEVEL=DEBUG makes the running server log everything the
# engine does: every LLM call with sizes/timing, parser and export engine
# decisions, per-segment progress. PDFTRANSL_LOG_FILE mirrors to a file.
PDFTRANSL_LOG_LEVEL = os.environ.get("PDFTRANSL_LOG_LEVEL", "INFO").strip().upper()
if PDFTRANSL_LOG_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR"):
    PDFTRANSL_LOG_LEVEL = "INFO"
PDFTRANSL_LOG_FILE = os.environ.get("PDFTRANSL_LOG_FILE", "").strip()

_log_handlers = ["console"] + (["file"] if PDFTRANSL_LOG_FILE else [])
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        **({"file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": PDFTRANSL_LOG_FILE,
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf-8",
            "formatter": "verbose",
        }} if PDFTRANSL_LOG_FILE else {}),
    },
    "root": {"handlers": _log_handlers, "level": PDFTRANSL_LOG_LEVEL},
    "loggers": {
        "pdftransl": {"level": PDFTRANSL_LOG_LEVEL, "propagate": True},
        "api": {"level": PDFTRANSL_LOG_LEVEL, "propagate": True},
        # keep framework noise moderate even in DEBUG
        "django": {"level": "INFO", "propagate": True},
        "urllib3": {"level": "INFO", "propagate": True},
        "PIL": {"level": "INFO", "propagate": True},
        "matplotlib": {"level": "INFO", "propagate": True},
        "fontTools": {"level": "INFO", "propagate": True},
    },
}
