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
   Nougat, marker, Docling, GROBID или PyMuPDF-фолбэк; auto-режим берёт
   лучший из установленных, а при сбое одного бэкенда `_parse` идёт по
   цепочке фолбэков (`parsing/base.py: fallback_backends`) — падение
   MinerU по таймауту не роняет задачу. Каждый бэкенд пишет в свой
   подкаталог; результат кэшируется по SHA-256 содержимого PDF
   (`parsing/cache.py`). Перед парсингом `parsing/scan_detect.py` +
   `parsing/text_quality.py` определяют, нужен ли OCR: страница без
   текстового слоя (скан) **или** битый текстовый слой (кракозябры —
   PUA-глифы, mojibake). Если да и есть vision-модель, документ уходит в
   `parsing/vlm_ocr_backend.py`: рендерит страницы в картинки и просит
   OCR-модель транскрибировать их в Markdown+LaTeX. OCR-модель выбирается
   независимо от модели перевода (`vision_model`/`vision_provider`), так
   что можно спарить специализированную OCR-модель (DeepSeek-OCR, GOT-OCR
   через vLLM — им даётся терсовый grounding-промпт) с любой LLM для
   перевода. Нет vision-модели — предупреждение в отчёт вместо молча
   пустого результата.
   - **memory guard** — после парсинга (перед загрузкой модели перевода)
     `pipeline._memory_guard` через `resources.wait_for_memory` ждёт, пока
     тяжёлый парсер (MinerU/Nougat/marker/Docling — они держат гигабайты в
     дочернем процессе) отдаст RAM, и только потом стартует Ollama/vLLM.
     Это лечит классический OOM: MinerU почти закончил, начинается загрузка
     LLM — и один из них падает от нехватки памяти. Порог —
     `min_free_memory_mb` (≈ размер модели), см. раздел «Управление
     ресурсами».
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
| `ParserBackend` | `parsing/base.py` | MinerU local/API, Nougat, marker, Docling, GROBID, vlm_ocr (сканы + спец-OCR), PyMuPDF | подкласс с `is_available()`/`parse()`, регистрация в `BACKENDS` |
| `BaseLLMClient` | `llm/base.py` | OpenAI-compat, Anthropic, Fallback, Fake | любой API |
| `BaseEmbedder` | `rag/embeddings.py` | hashing, sentence-transformers, API | — |
| экспорт-движок | `export/exporter.py` | pandoc, python-docx, chromium, weasyprint, latex | typst, LibreOffice |
| хранилище TM | `rag/store.py` | SQLite + cosine (numpy fast-path) | pgvector, Qdrant, sqlite-vec |

## Управление ресурсами (`resources.py`)

Модуль без внешних зависимостей — читает память через `psutil`, а если
его нет — через `/proc/meminfo` (Linux) или `sysctl`+`vm_stat` (macOS);
не смог ничего — один раз предупреждает и молча отключается, пайплайн не
падает.

- `memory_stats()` → `MemoryStats(total_mb, available_mb)` — снимок RAM.
- `wait_for_memory(min_free_mb, timeout, …)` — GC + опрос, пока не
  освободится память или не выйдет таймаут; никогда не бросает исключение.
  Используется memory guard'ом между парсингом и переводом (см. стадию 1).
- `Watchdog(stall_seconds, on_stall)` — контекст-менеджер с методом
  `beat()`; фоновый поток срабатывает один раз за «залипание», если между
  ударами прошло больше `stall_seconds`. `translator` бьёт `beat()` на
  каждый готовый сегмент, так что зависший/неотвечающий LLM или парсер
  даёт предупреждение (`stall_warning_seconds`), а не тихо висит.

Логи памяти (`_log_memory`) на входе и после парсинга включены при
`memory_guard=true`; если свободно меньше ~500 МБ, в отчёт попадает
`memory_warning` (его показывает и веб-интерфейс). Настройки —
`PDFTRANSL_MEMORY_GUARD`, `PDFTRANSL_MIN_FREE_MEMORY_MB`,
`PDFTRANSL_MEMORY_WAIT_TIMEOUT`, `PDFTRANSL_STALL_WARNING_SECONDS`.

## Возобновление (`translation/checkpoint.py`)

Каждый готовый сегмент дописывается в `.checkpoint.jsonl` (append-only,
рядом с выходными файлами задачи), с ключом по хешу источника + языковой
паре — обрыв на середине теряет максимум последнюю строку. При
`resume=true` перезапуск упавшей задачи (краш MinerU, сбой провайдера,
kill процесса) подхватывает уже переведённые сегменты и продолжает с
места обрыва — не переводя заново то, за что уже заплачено
токенами/временем. Чекпойнт удаляется по полному успеху, а частичный
прогон его сохраняет — чтобы было с чего возобновиться.
