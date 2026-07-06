# Архитектура pdftransl

Этот документ — карта проекта: что за чем выполняется и где что лежит.
Если вы хотите добавить свой парсер, провайдера или формат экспорта —
таблица точек расширения в конце покажет, какой интерфейс реализовать.

## Общая схема

```
  React SPA (frontend/)        Telegram-бот (bot/, aiogram)
        │  fetch /api/…               │  вызывает движок напрямую
        ▼                             ▼
  Django (backend/): api-приложение   │
   jobs / segments / corrections /    │
   rebuild / downloads / glossary     │
        │  Celery-таска или поток     │
        ▼                             ▼
  ┌────────────────────────────────────────────┐
  │           TranslationPipeline              │   (pdftransl/)
  └──┬─────────┬──────────┬─────────┬──────────┘
     │         │          │         │
 ┌───▼───┐ ┌───▼────┐ ┌───▼────┐ ┌──▼──────────┐
 │parsing│ │transla-│ │quality │ │ rag         │
 │MinerU │ │tion    │ │validat.│ │ TM+glossary │
 │PyMuPDF│ │LLM/VLM │ │reviewer│ │ embeddings  │
 │+cache │ │parallel│ │latex + │ │ retriever   │
 │+refs  │ │masking │ │backtr. │ │ domains     │
 └───────┘ └────────┘ └────────┘ └─────────────┘
     │         │          │         │
  ┌──▼─────────▼──────────▼─────────▼──┐
  │ export: HTML(KaTeX) / DOCX / PDF   │
  │ storage: SQLite (jobs, TM, gloss.) │
  └────────────────────────────────────┘
```

Три точки входа — CLI (`pdftransl`), Django API и телеграм-бот —
используют один и тот же движок и общие данные (память переводов,
глоссарий), поэтому правка, сделанная в веб-вычитке, улучшает и
переводы, запрошенные через бота.

## Стадии пайплайна

1. **parse** — `parsing/`: MinerU (локальный CLI / облачный API),
   marker, Docling или PyMuPDF-фолбэк; auto-режим берёт лучший из
   установленных. Картинки экспортируются в `Asset`; результат
   кэшируется по SHA-256 содержимого PDF (`parsing/cache.py`) — повторная
   загрузка того же файла не тратит GPU/API. Перед парсингом
   `parsing/scan_detect.py` проверяет, не скан ли это (страницы без
   текстового слоя, покрытые изображением): если да и есть vision-модель,
   документ автоматически уходит в `parsing/vlm_ocr_backend.py` — тот
   рендерит страницы в картинки и просит VLM транскрибировать их в
   Markdown+LaTeX (распознаёт и формулы; работает и локально через
   qwen2.5-vl). Если vision-модели нет — предупреждение в отчёт вместо
   молча пустого результата.
2. **split + references** — `parsing/splitter.py` разбивает Markdown на
   типизированные блоки; `mark_references()` находит секцию
   References/Bibliography (в т.ч. «Список литературы») и оставляет её
   без перевода — библиографические записи должны сохранять
   цитируемость. Заголовки-«границы» (Appendix и т.п.) снова включают
   перевод.
3. **context** — `translation/doc_context.py`: один LLM-проход строит
   саммари статьи (попадает в system-промпт каждого сегмента) и
   **авто-глоссарий** — до 25 терминов с переводами (JSON), которые
   принудительно подставляются в промпты. Оба шага не фатальны: при
   сбое пайплайн просто продолжает без них.
4. **mask + segment** — `masking.py` заменяет формулы/код/ссылки на
   плейсхолдеры `⟦PHn⟧`; блоки группируются в сегменты до
   `chunk_char_budget`.
5. **RAG** — `rag/`: точное совпадение в TM → перевод без LLM; похожие
   сегменты (cosine, доменный фильтр) → few-shot примеры; глоссарий из
   БД + документный авто-глоссарий → раздел терминологии.
6. **translate** — `translation/translator.py`: сегменты независимы и
   переводятся **параллельно** (ThreadPoolExecutor, `max_workers`).
   Контекст берётся с *исходной* стороны (хвост предыдущего
   сегмента-источника) — это разглаживает швы и остаётся
   параллельно-безопасным. Клиент — единый интерфейс `BaseLLMClient`;
   `FallbackClient` перебирает цепочку провайдеров при сбоях
   (`fallback_providers`), общий `RateLimiter` (`rpm_limit`) держит
   бюджет запросов/минуту на всю цепочку. После ответа: анмаскинг →
   валидаторы → ограниченный цикл исправлений с фидбеком модели.
7. **scoring (опция)** — `quality/scoring.py`: LLM-судья ставит каждому
   сегменту оценку 0–100; ниже порога — сегмент помечается и уходит на
   ревью; сводка в отчёте.
8. **review** — `quality/reviewer.py`: LLM-ревьюер перепроверяет
   проблемные сегменты (JSON-вердикт; при `structured_outputs` — честный
   JSON-mode), ревизия принимается только если не ломает плейсхолдеры.
9. **backtranslation (опция)** — `quality/backtranslation.py`: обратный
   перевод + косинус эмбеддингов оригинала и обратного перевода; низкая
   близость — предупреждение о потере смысла.
10. **assemble + latex fix** — сборка Markdown в исходном порядке;
    `quality/latex_check.py` находит синтаксически битые формулы, а
    `quality/latex_fix.py` просит LLM их починить — правка принимается
    только если проходит ту же проверку. При `bilingual=True`
    дополнительно собирается документ «цитата-оригинал → перевод».
11. **export** — `export/exporter.py`, движки по убыванию качества:
    - **DOCX**: pandoc (LaTeX → нативные формулы Word/OMML) →
      python-docx; в фолбэке формулы рендерятся в картинки через
      matplotlib mathtext (`export/formula_render.py`) — видна настоящая
      формула, а не сырой LaTeX (что вне подмножества mathtext — падает
      обратно в текст). Текст санитизируется от control-символов.
    - **PDF**: pandoc+xelatex → headless Chromium печать нашего
      KaTeX-HTML (`PDFTRANSL_CHROMIUM` — свой бинарь) → weasyprint;
    - **HTML**: собственный конвертер + KaTeX. KaTeX вшивается офлайн
      (`export/katex_assets.py`: инлайн CSS+JS, шрифты data-URI из
      `frontend/node_modules/katex/dist` или `PDFTRANSL_KATEX_DIR`),
      картинки — data-URI: файл полностью автономен и формулы рендерятся
      без сети (в т.ч. в Chromium-PDF). Без вендоренного KaTeX — фолбэк
      на CDN.
    - **LaTeX**: `export/latex.py` — компилируемый .tex проект.
    Недоступность движка не роняет пайплайн: в отчёте
    `export_engines` указана причина. При `render_check=True` итоговый
    HTML открывается в headless Chromium и ошибки KaTeX-рендера
    попадают в отчёт (`quality/render_check.py`).
12. **learn** — успешные пары уходят в TM (`origin=auto`, доменный тег);
    правки человека (`origin=human`) вытесняют автоматические, а
    короткие правки-термины дополнительно пополняют глоссарий.
13. **report** — `report.json`: статистика, проблемные сегменты,
    LaTeX-issues и починки, оценки судьи, экспорт-движки, авто-глоссарий.

## Django-бэкенд (`backend/`)

- `api.models.TranslationJob` — файл, параметры, статус/стадия/прогресс,
  пути результатов, QA-отчёт; `SegmentRecord` — пары
  «оригинал/перевод/правка» для вычитки.
- `api.services` — мост к движку: сборка `PipelineConfig` из задачи,
  запуск, сохранение сегментов; `save_correction()` кладёт правку в TM;
  `rebuild_outputs()` пересобирает MD/HTML/DOCX/PDF с учётом правок.
- `api.tasks.dispatch_job` — Celery при `USE_CELERY=1`, иначе фоновый
  поток (дев-режим без брокера).
- REST-эндпоинты без DRF (см. `api/urls.py`); прогресс — SSE-стрим
  `/api/jobs/<id>/events/` (поллинг остаётся фолбэком); React SPA
  раздаётся catch-all-вьюхой из `frontend/dist`.
- Защита: опциональный Bearer-токен на весь `/api/`
  (`PDFTRANSL_API_TOKEN`) и per-IP лимит загрузок
  (`PDFTRANSL_UPLOADS_PER_HOUR`).

## Telegram-бот (`bot/`)

aiogram v3, long polling. Приём PDF → прогресс редактированием
статус-сообщения (колбэк `on_stage` из потока через
`run_coroutine_threadsafe`) → отправка файлов выбранных форматов.
`/settings` — инлайн-клавиатура: язык, форматы, провайдер, двуязычный
режим, ревью; настройки чата персистятся в JSON.

## Точки расширения

| Интерфейс | Файл | Реализации | Как расширить |
|---|---|---|---|
| `ParserBackend` | `parsing/base.py` | MinerU local/API, marker, Docling, vlm_ocr (сканы), PyMuPDF | Nougat, GROBID |
| `BaseLLMClient` | `llm/base.py` | OpenAI-compat, Anthropic, Fallback, Fake | любой API |
| `BaseEmbedder` | `rag/embeddings.py` | hashing, sentence-transformers, API | — |
| экспорт-движок | `export/exporter.py` | pandoc, python-docx, chromium, weasyprint, latex | typst, LibreOffice |
| хранилище TM | `rag/store.py` | SQLite + cosine (numpy fast-path) | pgvector, Qdrant, sqlite-vec |
