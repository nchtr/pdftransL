# pdftransl — движок перевода научных PDF

Автоматический перевод научных статей со сложной вёрсткой: формулы,
таблицы, рисунки. PDF парсится в Markdown с LaTeX-формулами, картинки и
графики экспортируются отдельно, текст переводится локальными или
облачными LLM/VLM с самопроверкой, циклом исправлений и накоплением
translation memory (RAG). Готов к встраиванию в Python-бэкенды
(Django, FastAPI, Celery).

```
PDF ──> парсинг (MinerU / PyMuPDF) ──> Markdown + LaTeX + картинки
     ──> разбиение на блоки ──> маскирование формул/кода/ссылок
     ──> RAG-контекст (память переводов + глоссарий)
     ──> перевод LLM ──> валидаторы ──> цикл исправлений ──> LLM-ревью
     ──> сборка Markdown + отчёт QA + обучение памяти переводов
```

## Возможности

- **Парсинг** — плагинные бэкенды:
  - `mineru_local` — локальный MinerU CLI (формулы → LaTeX, таблицы, вёрстка);
  - `mineru_api` — облачный API MinerU (`MINERU_API_KEY`);
  - `pymupdf` — лёгкий фолбэк (текст + экспорт встроенных картинок);
  - `auto` — выбирает лучший доступный.
- **Защита формул** — LaTeX (`$...$`, `$$...$$`, окружения, `\cite`/`\ref`),
  код, картинки, URL и библиографические ссылки маскируются плейсхолдерами
  `⟦PH42⟧` до перевода и побайтово восстанавливаются после.
- **Провайдеры LLM** — один OpenAI-совместимый клиент покрывает OpenRouter,
  OpenAI, DeepSeek и локальные серверы (Ollama, vLLM, LM Studio,
  llama.cpp); отдельный клиент для Anthropic. VLM-описание рисунков.
- **Самоконтроль** — детерминированные валидаторы (целостность
  плейсхолдеров, форма таблиц, соотношение длин, доля непереведённого
  текста, баланс LaTeX-разделителей) + автоматический цикл исправлений +
  LLM-ревью проблемных сегментов. QA-отчёт в `report.json`.
- **Обучение и RAG** — translation memory на SQLite: точные совпадения
  переиспользуются без LLM, похожие сегменты подаются как few-shot
  примеры; правки человека (`origin=human`) имеют приоритет; глоссарий
  терминов принудительно подставляется в промпт; экспорт TM в JSONL
  (датасет для дообучения).
- **Интеграция в бэкенд** — `TranslationService` (submit / process /
  status / result) + готовый пример Django-приложения с Celery
  (`integrations/django_example/`).

## Установка

```bash
pip install -e .                  # ядро (requests + dotenv)
pip install -e ".[mineru]"        # + качественный парсинг научных PDF
pip install -e ".[pymupdf]"       # + лёгкий фолбэк-парсер
pip install -e ".[rag]"           # + нейросетевые эмбеддинги для TM
pip install -e ".[dev]"           # + pytest
```

Ключи — в `.env` (см. `.env.example`) или переменных окружения:

```env
MINERU_API_KEY=...        # облачный парсинг MinerU
OPENROUTER_API_KEY=...    # или OPENAI_API_KEY / ANTHROPIC_API_KEY / DEEPSEEK_API_KEY
```

Для локальных моделей ключи не нужны — достаточно запущенного
Ollama/vLLM/LM Studio.

## Быстрый старт (CLI)

```bash
# перевод PDF целиком (облако)
pdftransl translate data/input/article.pdf --provider openrouter --model "openrouter/auto"

# полностью локально: Ollama + локальный MinerU
pdftransl translate article.pdf --provider ollama --model qwen2.5:14b --backend mineru_local

# перевести уже распарсенный markdown
pdftransl translate-md article.md -o article.ru.md

# только парсинг (markdown + экспорт картинок)
pdftransl parse article.pdf -o data/output

# + VLM-описания рисунков (figures.json)
pdftransl translate article.pdf --describe-figures

# глоссарий и память переводов
pdftransl glossary add "attention head" "головка внимания"
pdftransl glossary import terms.csv
pdftransl tm stats
pdftransl tm export dataset.jsonl
```

## Использование из Python

```python
from pdftransl import PipelineConfig, TranslationService

config = PipelineConfig.from_env(provider="ollama", model="qwen2.5:14b")
service = TranslationService(config)

# синхронно
result = service.translate("data/input/article.pdf")
print(result.output_markdown_path, result.report["segments_failed"])

# асинхронно (веб-запрос -> воркер)
job_id = service.submit("data/input/article.pdf")   # из view
service.process(job_id)                              # из Celery-таски
print(service.status(job_id))                        # из polling-эндпоинта

# обучение: правка человека попадает в память переводов
service.add_correction(
    "The attention head computes...",
    "Головка внимания вычисляет...",
)
```

Интеграция с Django — готовое приложение и инструкция:
[`integrations/django_example/`](integrations/django_example/README.md).

## Результат работы

```
data/output/<имя_статьи>/
├── <имя_статьи>.md        # распарсенный оригинал
├── <имя_статьи>.ru.md     # перевод
├── assets/                # экспортированные картинки и графики
├── figures.json           # VLM-описания рисунков (опционально)
├── report.json            # QA-отчёт: проблемные сегменты, статистика
└── parse/                 # промежуточные файлы парсера
```

## Конфигурация

Все параметры — в `PipelineConfig` (`pdftransl/config.py`); основные
переменные окружения:

| Переменная | Значение |
|---|---|
| `PDFTRANSL_PROVIDER` | `openrouter` \| `openai` \| `anthropic` \| `deepseek` \| `ollama` \| `vllm` \| `lmstudio` \| `llamacpp` |
| `PDFTRANSL_MODEL` | имя модели у провайдера |
| `PDFTRANSL_BASE_URL` | свой OpenAI-совместимый endpoint |
| `PDFTRANSL_PARSER` | `auto` \| `mineru_local` \| `mineru_api` \| `pymupdf` |
| `PDFTRANSL_SOURCE_LANG` / `PDFTRANSL_TARGET_LANG` | языковая пара (по умолчанию `en` → `ru`) |
| `PDFTRANSL_USE_RAG` / `PDFTRANSL_REVIEW` / `PDFTRANSL_LEARN` | `true`/`false` |
| `PDFTRANSL_DB` | путь к SQLite (jobs + TM + глоссарий) |

## Документация

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — устройство пайплайна и модулей.
- [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) — план улучшений / роадмап.
- [`docs/LEGACY_HERMES.md`](docs/LEGACY_HERMES.md) — прежняя инструкция
  Hermes-демо (скрипты `run_parser_agent.py` / `run_translator_agent.py`
  по-прежнему работают и используют новый движок).

## Тесты

```bash
python -m pytest tests/ -q    # офлайн, без сети и API-ключей
```
