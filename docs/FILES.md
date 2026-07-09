# Карта файлов проекта

Что за что отвечает — файл за файлом. Порядок: движок → бэкенд → бот →
фронтенд → инфраструктура. Схема взаимодействия крупными блоками — в
[ARCHITECTURE.md](ARCHITECTURE.md).

## Движок (`pdftransl/`)

| Файл | За что отвечает |
|---|---|
| `__init__.py` | Публичный API пакета: реэкспорт `TranslationPipeline`, `TranslationService`, `PipelineConfig`, моделей данных; `__version__`. |
| `__main__.py` | Точка входа `python -m pdftransl` — делегирует в CLI. |
| `cli.py` | CLI-команды: `translate`, `translate-md`, `parse`, `glossary`, `tm`, `jobs`, `engines`. Разбор флагов → `PipelineConfig` → вызов сервиса. |
| `config.py` | Вся конфигурация: `PipelineConfig` (dataclass со ~50 настройками), `ProviderConfig`, пресеты провайдеров (ollama/openrouter/…/deepseek_ocr), чтение переменных окружения `PDFTRANSL_*`, эвристика «модель умеет vision?» по имени. |
| `models.py` | Общие структуры данных: `Block` (типизированный блок Markdown), `Segment` (единица перевода; `final_text()` с откатами на оригинал), `QAIssue`, `ParsedDocument`, `Asset`, `JobResult`. |
| `masking.py` | Защита хрупкого содержимого: маскировка формул/кода/ссылок/цитирований плейсхолдерами `⟦PHn⟧` до перевода и восстановление после (`unmask` — до неподвижной точки, с отчётом о потерянных/выдуманных токенах). |
| `pipeline.py` | Оркестратор всего: parse → split → context → translate → QA-стадии → export → learn → report. Memory guard между парсером и моделью, дискретная запись результата, пауза, деградация стадий по-одной. |
| `service.py` | Фасад без привязки к фреймворку: `submit → process → status` для интеграции в любой бэкенд; правки человека → TM/глоссарий. |
| `progress.py` | Точный прогресс: план стадий под конкретный конфиг (`build_stage_plan`), `StageTracker` (доля внутри стадии → общий 0..1), `estimate_eta_seconds` (линейная оценка времени до конца). |
| `resources.py` | Ресурсы: чтение свободной RAM (psutil → /proc/meminfo → vm_stat), `wait_for_memory` (ожидание перед загрузкой модели — анти-OOM), `Watchdog` (детект зависшего LLM/парсера). |
| `logging_setup.py` | Настройка логов: уровень/файл из env, `set_level()` для смены «на лету» из веб-настроек. |
| `exceptions.py` | Иерархия исключений: `PdftranslError` → `ParserError`/`ParserUnavailableError`/`LLMError`… |

### Парсинг PDF (`pdftransl/parsing/`)

| Файл | За что отвечает |
|---|---|
| `base.py` | Интерфейс `ParserBackend`, реестр бэкендов, auto-выбор лучшего установленного, цепочка фолбэков (`fallback_backends`). |
| `mineru_local.py` | MinerU как локальный CLI-подпроцесс (лучшие формулы; таймаут `parser_timeout`). |
| `mineru_api.py` | Облачный API mineru.net: загрузка PDF, опрос статуса, скачивание результата. |
| `nougat_backend.py` | Nougat (Meta) — сквозной OCR научных статей в Markdown (нужен GPU). |
| `marker_backend.py` | marker — быстрый парсер с поддержкой LaTeX-формул. |
| `docling_backend.py` | Docling (IBM) — силён на таблицах. |
| `grobid_backend.py` | GROBID через HTTP-сервер: TEI → Markdown, точная структура/библиография. |
| `vlm_ocr_backend.py` | OCR vision-моделью: рендер страниц в PNG → транскрипция в Markdown+LaTeX (по одной картинке за раз). Постраничный прогресс + инкрементальная запись на диск, жёсткий таймаут на страницу (зависшая отваливается за минуты). Понимает спец-OCR модели (DeepSeek-OCR — терсовый grounding-промпт), чистит утёкшие стоп-токены (`<|im_end|>`, `NoneNone`…), после OCR выгружает локальную модель из памяти (анти-OOM). |
| `pymupdf_backend.py` | PyMuPDF — мгновенный фолбэк: голый текст без распознавания формул. |
| `cache.py` | Кэш парсинга по SHA-256 содержимого PDF — повторная загрузка того же файла не парсится заново. |
| `scan_detect.py` | Детектор «PDF-скан» (нет текстового слоя) и метрики по страницам — роутинг в OCR. |
| `text_quality.py` | Детектор «кракозябр»: PUA-глифы, mojibake, доля осмысленных символов; несовпадение языка/письменности. |
| `splitter.py` | Markdown → типизированные блоки (заголовок/абзац/таблица/формула/код/картинка); `mark_references` — библиография не переводится; `assemble` — сборка обратно. |

### Перевод (`pdftransl/translation/`)

| Файл | За что отвечает |
|---|---|
| `translator.py` | Ядро перевода: группировка блоков в сегменты (`build_segments`, гигантские абзацы режутся по предложениям), параллельный перевод партиями, цикл исправлений с фидбеком валидаторов, доперевод остатков на языке оригинала (`retranslate_residual`), пауза, чекпойнты. |
| `prompts.py` | Все промпты: системный переводческий (правила плейсхолдеров/структуры), repair, ревью, глоссарий/примеры TM в system, имена языков. |
| `checkpoint.py` | Возобновление: append-only JSONL готовых сегментов рядом с результатом; ключ = хеш источника + языковая пара; переживает обрыв на полуслове. |
| `doc_context.py` | Документный контекст одним LLM-проходом: саммари статьи + авто-глоссарий терминов (JSON), оба не фатальны при сбое. |
| `figures.py` | Описание рисунков vision-моделью (опция `describe_figures`). |

### Контроль качества (`pdftransl/quality/`)

| Файл | За что отвечает |
|---|---|
| `validators.py` | Детерминированные проверки сегмента: пустой перевод, длина, остатки исходного языка, структура заголовков/таблиц, чётность `$$`; `document_report` — сводка. |
| `reviewer.py` | LLM-ревьюер проблемных сегментов: JSON-вердикт ok/revised; ревизия принимается только если не теряет содержимое плейсхолдеров. |
| `scoring.py` | LLM-судья: оценка 0–100 каждому сегменту, ниже порога — флаг на ревью. |
| `backtranslation.py` | Обратный перевод + косинус эмбеддингов: ловит потерю смысла. |
| `latex_check.py` | Синтаксическая проверка формул без TeX: баланс скобок, парность `\begin/\end`, чётность `$$`. |
| `latex_fix.py` | LLM-починка битых формул; правка принимается только если проходит ту же проверку. |
| `document_repair.py` | LLM-починка вёрстки итогового документа (артефакты парсера: порванные абзацы, уровни заголовков, порядок кусков); по кускам, с гарантией сохранности контента (`fix_layout`). |
| `render_check.py` | Открывает готовый HTML в headless Chromium и считает ошибки KaTeX-рендера. |

### Память переводов и RAG (`pdftransl/rag/`)

| Файл | За что отвечает |
|---|---|
| `store.py` | TM в SQLite: точные совпадения (human-правки приоритетнее), косинусный поиск похожих (numpy fast-path), доменный фильтр, экспорт датасета для дообучения. |
| `glossary.py` | Глоссарий терминов в SQLite: добавление/поиск вхождений в тексте/CSV-импорт. |
| `retriever.py` | Сборка RAG-контекста сегмента: точное совпадение → похожие примеры → термины глоссария. |
| `embeddings.py` | Эмбеддеры за одним интерфейсом: hashing (без зависимостей), sentence-transformers, OpenAI-совместимый API; `cosine`. |

### LLM-клиенты (`pdftransl/llm/`)

| Файл | За что отвечает |
|---|---|
| `base.py` | Интерфейс `BaseLLMClient` (`chat`, `supports_vision`), `vision_message` — сборка мультимодального сообщения с картинкой. |
| `openai_compat.py` | Клиент любого OpenAI-совместимого `/chat/completions`: OpenAI, OpenRouter, DeepSeek, Ollama, vLLM, LM Studio…; ретраи, Retry-After, DEBUG-телеметрия. |
| `anthropic_client.py` | Нативный клиент Anthropic Messages API (system отдельно, свой формат картинок). |
| `fallback.py` | `FallbackClient` — цепочка провайдеров: упал первый → пробуем следующий. |
| `ratelimit.py` | `RateLimiter` (rpm-бюджет на все потоки) и `CooldownGate` (одно 429 ставит на паузу всех; экспоненциальный штраф, уважение Retry-After). |
| `registry.py` | Фабрика клиента по имени провайдера. |
| `fake.py` | Детерминированный фейк для тестов/dry-run. |

### Экспорт (`pdftransl/export/`)

| Файл | За что отвечает |
|---|---|
| `exporter.py` | Оркестрация форматов с фолбэками движков: DOCX (pandoc → python-docx), PDF (pandoc+xelatex → Chromium → xelatex → weasyprint), HTML, LaTeX; честный отчёт какой движок сработал/почему нет. |
| `html.py` | Markdown → автономный HTML: собственный конвертер + KaTeX, картинки в data-URI. |
| `katex_assets.py` | Вшивание KaTeX офлайн: инлайн CSS/JS, шрифты data-URI; фолбэк на CDN. |
| `latex.py` | Markdown → компилируемый .tex: секции, tabular, figure; экранирование спецсимволов одним проходом. |
| `docx_native.py` | DOCX без pandoc: python-docx + формулы картинками (matplotlib mathtext). |
| `formula_render.py` | Рендер LaTeX → PNG через matplotlib mathtext (для DOCX-фолбэка). |

### Хранилище задач (`pdftransl/storage/`)

| Файл | За что отвечает |
|---|---|
| `repository.py` | SQLite-репозиторий задач для CLI/сервиса (без Django): create/get/update/list. |

## Django-бэкенд (`backend/`)

| Файл | За что отвечает |
|---|---|
| `config/settings.py` | Настройки Django из env: пути данных, БД, Celery, лимиты загрузок, токен API. |
| `config/urls.py` | Корневой роутинг: `/api/…`, админка, catch-all на React SPA. |
| `config/celery.py` | Инициализация Celery-приложения (продакшен-очередь). |
| `config/asgi.py` / `wsgi.py` | Стандартные точки входа серверов. |
| `api/models.py` | `TranslationJob` (статусы вкл. paused, `stage_plan`, `started_at`, ETA), `SegmentRecord` (вычитка; `final_text` с откатами), `ServerConfig` (настройки «на лету» одним JSON). |
| `api/views.py` | Все JSON-эндпоинты: задачи (загрузка/список/удаление), SSE-стримы (одна задача + весь список), пауза/резюме, сегменты/правки, пересборка, скачивание, глоссарий, TM-статистика, серверные настройки, отдача SPA. |
| `api/services.py` | Мост Django ↔ движок: сборка `PipelineConfig` из настроек+опций задачи, `run_job` (с колбэками прогресса и паузы), `compute_stage_plan`, `pause_job`/`prepare_resume`, правки → TM, пересборка файлов. |
| `api/tasks.py` | Диспатч: Celery-таска при `USE_CELERY=1`, иначе фоновый поток. |
| `api/middleware.py` | CORS и опциональный Bearer-токен на `/api/` (`?token=` — для EventSource). |
| `api/urls.py` | Роутинг приложения api. |
| `api/admin.py` | Регистрация моделей в админке. |
| `api/migrations/` | Миграции схемы (не редактируются руками). |

## Телеграм-бот (`bot/`)

| Файл | За что отвечает |
|---|---|
| `main.py` | aiogram v3: приём PDF → прогресс редактированием статус-сообщения → файлы результата; `/settings` на инлайн-кнопках. Работает через `service.process` — статусы задач видны и в CLI/веб. |
| `settings_store.py` | Per-chat настройки (язык/форматы/провайдер/опции) в JSON-файле. |
| `__main__.py` | `python -m bot`. |

## Фронтенд (`frontend/src/`)

| Файл | За что отвечает |
|---|---|
| `App.jsx` | Каркас: вкладки Перевод/Настройки/Глоссарий, живой список задач по SSE (`/api/jobs/events/`) с фолбэком на поллинг. |
| `api.js` | Тонкий клиент REST API (fetch + обработка ошибок). |
| `format.js` | Форматирование ETA («~3 мин», «меньше минуты»). |
| `components/UploadForm.jsx` | Форма загрузки: языки, провайдер/модель, выбор парсера PDF и OCR-модели, форматы, опции пайплайна. |
| `components/JobList.jsx` | Список задач: статус-бейджи, прогресс, ETA, удаление. |
| `components/JobDetail.jsx` | Карточка задачи: SSE-прогресс, степпер стадий, ETA, пауза/продолжить, предупреждения из отчёта, скачивание, вычитка, пересборка. |
| `components/StageStepper.jsx` | Визуализация плана стадий: пройдено/текущая с %/ожидает/пауза/ошибка. |
| `components/SegmentReview.jsx` | Вычитка: оригинал/перевод рядом, правка сегмента → TM. |
| `components/SettingsPanel.jsx` | Серверные настройки «на лету» (PUT `/api/settings/`). |
| `components/GlossaryPanel.jsx` | Просмотр/добавление/удаление терминов глоссария. |
| `main.jsx`, `vite.config.js`, `styles.css` | Точка входа, сборка Vite (прокси на :8000 в dev), стили. |

## Инфраструктура

| Файл | За что отвечает |
|---|---|
| `quickstart.sh` | Установка одной командой: venv (Python 3.12/3.13), зависимости, MinerU по флагу, pandoc/Chromium по желанию, сборка фронтенда, опрос Ollama и выбор модели, автонастройка .env (включая анти-OOM под размер модели), миграции. |
| `quickstart.ps1` | То же самое для Windows (PowerShell 5.1+): py-лаунчер, winget-подсказки для pandoc/Node/Ollama, RAM через CIM, опрос Ollama по HTTP API, анти-OOM в .env. |
| `docker-compose.yml` | Контейнеры: web + celery-воркер + redis. |
| `.env.example` | Аннотированный список всех переменных окружения. |
| `pyproject.toml` | Пакет, зависимости, extras (pymupdf/export/backend/bot/dev), версия. |
| `tests/` | ~180 офлайн-тестов без ключей: маскинг, сплиттер, переводчик, пайплайн, пауза/резюме, прогресс/батчи, ресурсы/OCR, экспорт, TM/RAG, валидаторы, троттлинг, регрессии аудитов. |
| `docs/` | ARCHITECTURE (устройство), OLLAMA (локальные модели), FINETUNING (LoRA на вашей TM), IMPROVEMENTS (история версий/планы), FILES (этот файл). |
