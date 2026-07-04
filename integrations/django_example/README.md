# Интеграция pdftransl в Django

Пример готового Django-приложения `pdftranslator`: загрузка PDF через
API, асинхронный перевод в Celery-воркере, поллинг статуса, скачивание
результата и приём правок пользователя в translation memory.

## Подключение

1. Установите зависимости:

```bash
pip install -e ".[django]"   # из корня репозитория: Django + celery
```

2. Скопируйте приложение `pdftranslator/` в свой проект (или добавьте
   `integrations/django_example` в `PYTHONPATH`).

3. `settings.py`:

```python
INSTALLED_APPS = [
    # ...
    "pdftranslator",
]

MEDIA_ROOT = BASE_DIR / "media"

# pdftransl
PDFTRANSL_DB = BASE_DIR / "data" / "pdftransl.db"
PDFTRANSL_OUTPUT_DIR = BASE_DIR / "data" / "output"

# Celery (стандартная настройка)
CELERY_BROKER_URL = "redis://localhost:6379/0"
```

Ключи провайдеров задаются переменными окружения процесса воркера:
`OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`MINERU_API_KEY` — или ничего, если используется локальный
Ollama/vLLM (`PDFTRANSL_PROVIDER=ollama`).

4. `urls.py` проекта:

```python
from django.urls import include, path

urlpatterns = [
    # ...
    path("api/translate/", include("pdftranslator.urls")),
]
```

5. Миграции и запуск:

```bash
python manage.py makemigrations pdftranslator
python manage.py migrate
celery -A yourproject worker -l info   # отдельный процесс
python manage.py runserver
```

## API

| Метод | URL | Описание |
|---|---|---|
| POST | `/api/translate/jobs/` | multipart `file=<pdf>` (+`source_lang`, `target_lang`, `provider`, `model`) → `202 {job_id}` |
| GET | `/api/translate/jobs/<id>/` | статус: `status`, `stage`, `progress`, `report` |
| GET | `/api/translate/jobs/<id>/download/` | скачать переведённый Markdown |
| POST | `/api/translate/corrections/` | JSON `{source, corrected}` — правка в translation memory |

Пример:

```bash
curl -F "file=@article.pdf" -F "target_lang=ru" http://localhost:8000/api/translate/jobs/
curl http://localhost:8000/api/translate/jobs/<job_id>/
curl -OJ http://localhost:8000/api/translate/jobs/<job_id>/download/
```

## Замечания для продакшена

- Добавьте аутентификацию (`LoginRequiredMixin`/DRF permissions) — пример
  намеренно открыт.
- MinerU-парсинг тяжёлый (GPU желателен): выделите отдельную Celery-очередь
  `-Q pdf_parse` и ограничьте concurrency.
- Для нескольких воркеров на разных машинах перенесите translation memory
  с SQLite на PostgreSQL + pgvector (см. `docs/IMPROVEMENTS.md`).
- Файлы результата отдавайте через nginx `X-Accel-Redirect`, а не FileResponse.
