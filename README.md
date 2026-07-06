# pdftransl — платформа перевода научных PDF

Автоматический перевод научных статей со сложной вёрсткой: формулы,
таблицы, рисунки. PDF парсится в Markdown с LaTeX, картинки и графики
экспортируются отдельно, текст переводится локальными или облачными
LLM/VLM параллельно и с самопроверкой, результат собирается обратно в
**Markdown / HTML / DOCX / PDF** с сохранением структуры и
форматирования. Накопленные переводы образуют translation memory
(RAG + дообучение на правках человека).

Состав монорепозитория:

| Каталог | Что это |
|---|---|
| `pdftransl/` | движок (Python-пакет + CLI `pdftransl`) |
| `backend/` | Django-проект: JSON API, задачи, вычитка, админка |
| `frontend/` | React SPA: загрузка, опции, прогресс, вычитка, скачивание |
| `bot/` | Telegram-бот (aiogram v3) |
| `docker-compose.yml` | web + celery-worker + redis + бот + ollama |

```
PDF ─> парсинг (MinerU / PyMuPDF, кэш по хэшу) ─> Markdown + LaTeX + ассеты
    ─> блоки (References не переводятся) ─> саммари статьи + авто-глоссарий
    ─> маскирование формул/кода/ссылок ─> RAG (память переводов + глоссарий)
    ─> параллельный перевод LLM (fallback-цепочка провайдеров)
    ─> валидаторы ─> цикл исправлений ─> LLM-ревью ─> back-translation чек
    ─> сборка MD (+двуязычный) ─> LaTeX-проверка ─> экспорт HTML/DOCX/PDF
    ─> QA-отчёт ─> обучение памяти переводов
```

## Возможности движка

- **Парсинг**: MinerU локально / облачный API (формулы → LaTeX, таблицы,
  вёрстка) или PyMuPDF-фолбэк; экспорт картинок и графиков; кэш
  результатов по содержимому PDF.
- **Защита формул**: LaTeX, код, ссылки, URL и цитирования маскируются
  плейсхолдерами `⟦PHn⟧` и восстанавливаются побайтово; целостность —
  жёсткий валидатор с циклом исправлений.
- **Провайдеры**: OpenRouter, OpenAI, Anthropic, DeepSeek и локальные
  Ollama/vLLM/LM Studio/llama.cpp через один клиент; **fallback-цепочка**
  (локальный → облачный) при сбоях; VLM-описания рисунков.
- **Качество**: параллельный перевод сегментов; саммари документа и
  **авто-глоссарий** в промпте (согласованная терминология); контекст
  предыдущего сегмента; детерминированные валидаторы; LLM-ревью;
  опциональный **back-translation** чек; синтаксическая проверка LaTeX
  итогового документа; список литературы остаётся в оригинале.
- **Обучение/RAG**: translation memory (SQLite + эмбеддинги) с доменными
  тегами; точные совпадения — без вызова LLM; правки человека имеют
  приоритет; экспорт JSONL-датасета для LoRA-дообучения.
- **Экспорт**: HTML (KaTeX), DOCX (pandoc → нативные формулы Word, либо
  python-docx), PDF (pandoc+xelatex → Chromium print → weasyprint), плюс
  двуязычный Markdown для вычитки.

## Быстрый старт

### 1. Движок + CLI

```bash
pip install -e ".[pymupdf,export,dev]"    # + mineru для научных PDF
cp .env.example .env                       # ключи провайдеров

pdftransl translate article.pdf --formats html,docx,pdf
pdftransl translate article.pdf --provider ollama --model qwen2.5:14b \
    --fallback openrouter --workers 8 --bilingual
pdftransl engines        # какие экспортные движки доступны
pdftransl tm export dataset.jsonl
```

### 2. Django-бэкенд + React UI

```bash
pip install -e ".[pymupdf,export,backend]"
cd frontend && npm install && npm run build && cd ..   # собрать SPA
cd backend
python manage.py migrate
python manage.py runserver        # http://localhost:8000
```

Без Celery задачи выполняются в фоновых потоках (для разработки этого
достаточно). Для продакшена: `USE_CELERY=1`, redis и
`celery -A config worker`.

Разработка фронтенда с горячей перезагрузкой:
`cd frontend && npm run dev` (vite проксирует `/api` на :8000).

**API**: `POST /api/jobs/` (multipart: `file`, `source_lang`,
`target_lang`, `provider`, `model`, `options` JSON) → `GET
/api/jobs/<id>/` (статус/прогресс) → `GET /api/jobs/<id>/download/
?format=md|html|docx|pdf|bilingual|report`. Вычитка: `GET
/api/jobs/<id>/segments/`, `POST /api/jobs/<id>/segments/<n>/correct/`,
`POST /api/jobs/<id>/rebuild/` — пересборка всех форматов с правками.
Плюс `/api/providers/`, `/api/glossary/`, `/api/tm/stats/`.

### 3. Telegram-бот

```bash
pip install -e ".[bot]"
TELEGRAM_BOT_TOKEN=... python -m bot
```

Пользователь присылает PDF — бот показывает прогресс по стадиям и
возвращает файлы в выбранных форматах. `/settings` — язык, форматы,
провайдер, двуязычный режим (инлайн-кнопки, настройки сохраняются).

### 4. Docker

```bash
cp .env.example .env               # ключи
docker compose up --build          # web :8000 + worker + redis
docker compose --profile bot up    # + телеграм-бот
docker compose --profile local up  # + ollama для локальных моделей
```

## Результат работы

```
data/output/<имя>/
├── <имя>.md                  # распарсенный оригинал
├── <имя>.ru.md               # перевод (Markdown)
├── <имя>.ru.html             # HTML с KaTeX
├── <имя>.ru.docx             # DOCX (pandoc: нативные формулы)
├── <имя>.ru.pdf              # PDF (pandoc/Chromium/weasyprint)
├── <имя>.ru.bilingual.md     # двуязычный (опция)
├── assets/                    # картинки и графики
├── figures.json               # VLM-описания рисунков (опция)
└── report.json                # QA-отчёт
```

## Конфигурация

Всё управляется `PipelineConfig` / переменными окружения (полный список
в `.env.example`): провайдер и модель, fallback-цепочка, число
параллельных воркеров, форматы экспорта, RAG/ревью/обучение,
двуязычный режим, кэш парсинга, домен TM.

## Документация

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — стадии пайплайна, модули, точки расширения.
- [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) — что уже реализовано из роадмапа и что дальше.

## Тесты

```bash
python -m pytest tests/ -q     # офлайн, без сети и ключей (60+ тестов)
```
