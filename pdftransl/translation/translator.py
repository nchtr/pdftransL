"""Движок перевода: сегментация, дедупликация и параллельный LLM-перевод
с циклом самопроверки. Переработан в v0.18 с упором на оптимизацию и
стабильность.

Как устроено:

* блоки группируются в сегменты (гигантские абзацы режутся по
  предложениям, чтобы не перегружать модель);
* одинаковые сегменты (колонтитулы, повторяющиеся подписи — типично для
  OCR-документов) переводятся ОДИН раз на документ, копии берут готовый
  результат из кэша прогона;
* сегменты переводятся параллельно партиями через один общий пул
  потоков (без пересоздания пула на каждую партию);
* каждый ответ проходит валидаторы, проблемы скармливаются модели
  обратно в ограниченном цикле исправлений; если модель дважды вернула
  один и тот же ответ — выходим сразу, дальнейшие попытки бессмысленны;
* сетевые бэкоффы живут в LLM-клиенте (ретраи, Retry-After, общий
  кулдаун на 429) — цикл исправлений НЕ спит вслепую между попытками.

Сегменты независимы по построению (плейсхолдеры уникальны на весь
документ, контекст берётся с *исходной* стороны), поэтому при
``max_workers > 1`` их можно переводить параллельно. Пауза кооперативна:
``should_pause`` опрашивается перед каждой партией и после каждого
завершённого сегмента; недоделанное подхватит чекпойнт при
возобновлении.
"""

from __future__ import annotations

import logging
import re
import threading
import time as _time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import BaseLLMClient
from pdftransl.masking import Masker, unmask
from pdftransl.models import Block, BlockType, QAIssue, Segment, new_id
from pdftransl.quality.validators import residual_source_ratio, validate_segment
from pdftransl.translation.prompts import (
    REPAIR_USER,
    build_translation_system,
    build_user_message,
)

logger = logging.getLogger(__name__)

ProgressCb = Callable[[int, int, str], None]


# ---------------------------------------------------------------------------
# Сегментация
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def _split_oversized(text: str, char_budget: int) -> list[str]:
    """Разрезать абзац, который ОДИН больше бюджета сегмента.

    Одиночный блок-монстр (типичный источник — битый PDF, чья страница
    извлеклась одним абзацем без пустых строк) раньше уходил в LLM
    одним запросом: переполнял контекст/max_tokens и возвращался
    обрезанным. Режем по границам предложений, упаковывая их до
    бюджета; предложение длиннее бюджета остаётся целым (разрез посреди
    предложения вредит переводу сильнее длинного запроса), если только
    оно не в 4 раза больше — тогда фолбэк на границы строк. Посреди
    слова не режем никогда.
    """
    if len(text) <= char_budget:
        return [text]
    pieces = _SENTENCE_SPLIT_RE.split(text)
    if len(pieces) == 1 and len(text) > 4 * char_budget:
        pieces = [ln for ln in text.splitlines() if ln.strip()] or [text]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > char_budget:
            chunks.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip() if current else piece
    if current:
        chunks.append(current)
    return chunks or [text]


def build_segments(
    blocks: list[Block],
    masker: Masker,
    char_budget: int = 4000,
) -> list[Segment]:
    """Сгруппировать блоки в сегменты перевода.

    Последовательные переводимые блоки сливаются, пока не набран
    ``char_budget``; непереводимые становятся pass-through-сегментами с
    сохранением порядка документа. Одиночный *абзац* крупнее бюджета
    режется по предложениям (см. :func:`_split_oversized`), чтобы один
    гигантский блок не перегрузил LLM; таблицы и заголовки не режутся
    никогда — частичная таблица ломает и markdown-структуру, и
    валидатор количества строк.
    """
    segments: list[Segment] = []
    buf: list[Block] = []
    buf_texts: list[str] = []
    buf_len = 0

    def flush_translate() -> None:
        nonlocal buf, buf_texts, buf_len
        if not buf:
            return
        source = "\n\n".join(buf_texts)
        masked = masker.mask(source)
        segments.append(
            Segment(
                id=new_id("seg_"),
                kind="translate",
                source_text=source,
                block_indices=sorted({b.index for b in buf}),
                masked_text=masked.text,
                placeholders=masked.mapping,
            )
        )
        buf = []
        buf_texts = []
        buf_len = 0

    def add_translatable(block: Block, text: str) -> None:
        nonlocal buf_len
        if buf and buf_len + len(text) > char_budget:
            flush_translate()
        buf.append(block)
        buf_texts.append(text)
        buf_len += len(text) + 2

    for block in blocks:
        if block.translatable and block.text.strip():
            if (
                block.type == BlockType.PARAGRAPH
                and len(block.text) > char_budget
            ):
                for chunk in _split_oversized(block.text, char_budget):
                    add_translatable(block, chunk)
                    flush_translate()  # каждый крупный кусок — своим сегментом
            else:
                add_translatable(block, block.text)
        else:
            flush_translate()
            segments.append(
                Segment(
                    id=new_id("seg_"),
                    kind="pass",
                    source_text=block.text,
                    block_indices=[block.index],
                )
            )
    flush_translate()
    return segments


# ---------------------------------------------------------------------------
# Кэш прогона: одинаковые сегменты переводим один раз
# ---------------------------------------------------------------------------

class _RunCache:
    """Потокобезопасный кэш «источник -> перевод» на время одного
    документа.

    OCR-документы и журнальные вёрстки полны повторов: колонтитулы,
    подписи «Table N», дисклеймеры на каждой странице. Раньше каждый
    повтор стоил отдельного LLM-запроса; теперь первый удачный перевод
    переиспользуется. Кэшируются только чистые (ok) результаты — мусор
    тиражировать нельзя. Ёмкость ограничена, чтобы документ-гигант не
    раздул память.
    """

    _MAX_ENTRIES = 4096

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, str] = {}

    def get(self, source_text: str) -> Optional[str]:
        with self._lock:
            return self._data.get(source_text)

    def put(self, source_text: str, translation: str) -> None:
        with self._lock:
            if len(self._data) < self._MAX_ENTRIES:
                self._data.setdefault(source_text, translation)


# ---------------------------------------------------------------------------
# Переводчик
# ---------------------------------------------------------------------------

class Translator:
    """Переводит сегменты: защита плейсхолдерами, валидация и
    ограниченный цикл исправлений (самоконтроль)."""

    def __init__(
        self,
        client: BaseLLMClient,
        config: PipelineConfig,
        retriever=None,   # rag.retriever.RAGContextBuilder | None
        checkpoint=None,  # translation.checkpoint.Checkpoint | None
    ):
        self.client = client
        self.config = config
        self.retriever = retriever
        self.checkpoint = checkpoint
        # Документный контекст — пайплайн задаёт его один раз на документ.
        self.doc_summary: str = ""
        self.doc_terms: list[dict[str, str]] = []
        # Кэш повторяющихся сегментов; обновляется на каждый документ.
        self._run_cache = _RunCache()
        # Скомпилированные шаблоны терминов документа (см. _compiled_terms).
        self._term_patterns_src: Optional[list] = None
        self._term_patterns: list[tuple[re.Pattern, dict[str, str]]] = []

    # -- вспомогательное -------------------------------------------------
    def _compiled_terms(self) -> list[tuple[re.Pattern, dict[str, str]]]:
        """Regex-шаблоны терминов документа, скомпилированные один раз.

        Раньше ``re.escape`` + ``re.search`` выполнялись на КАЖДЫЙ
        сегмент для каждого термина; на документе в сотни сегментов с
        десятками терминов это тысячи лишних компиляций. Кэш
        инвалидируется по identity списка ``doc_terms`` — пайплайн
        присваивает его целиком один раз на документ.
        """
        if self._term_patterns_src is not self.doc_terms:
            self._term_patterns_src = self.doc_terms
            self._term_patterns = []
            for t in self.doc_terms:
                term = (t.get("term") or "").strip()
                if not term:
                    continue
                # Точный поиск по границам слов (\b), чтобы 'ion'
                # не находился внутри 'transformation'.
                pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
                self._term_patterns.append((pattern, t))
        return self._term_patterns

    def _chat(self, messages: list[dict]) -> str:
        """Один вызов LLM с параметрами конфига (общая точка для
        перевода, починки и доперевода)."""
        return self.client.chat(
            messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_output_tokens,
        )

    def _build_messages(
        self, segment: Segment, source_context: str
    ) -> list[dict]:
        """Собрать system+user сообщения для перевода сегмента."""
        doc_hits = [
            t for pattern, t in self._compiled_terms()
            if pattern.search(segment.source_text)
        ]
        glossary_terms = doc_hits + (segment.glossary_hits or [])
        system = build_translation_system(
            self.config.source_lang,
            self.config.target_lang,
            glossary_terms=glossary_terms or None,
            tm_examples=segment.tm_examples or None,
            doc_summary=self.doc_summary or None,
        )
        user = build_user_message(segment.masked_text, source_context or None)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    # -- один сегмент ------------------------------------------------
    def translate_segment(
        self, segment: Segment, source_context: str = ""
    ) -> Segment:
        if segment.kind != "translate":
            return segment

        started = _time.monotonic()
        cfg = self.config

        # Повтор внутри документа: этот же источник уже переведён в этом
        # прогоне (колонтитул, повторная подпись) — берём из кэша, LLM
        # не нужен.
        cached = self._run_cache.get(segment.source_text)
        if cached is not None:
            segment.translation = cached
            segment.issues.append(
                QAIssue("dedup", "reused translation of an identical segment "
                        "from this run", "info")
            )
            logger.debug("segment %s: run-cache hit", segment.id)
            return segment

        # Возобновление: прошлый прогон этого документа уже перевёл
        # сегмент — берём из чекпойнта, не трогая LLM.
        if self.checkpoint is not None:
            done = self.checkpoint.get(segment.source_text)
            if done is not None:
                segment.translation = done
                segment.issues.append(
                    QAIssue("resumed", "reused from checkpoint (resumed job)", "info")
                )
                logger.debug("segment %s: resumed from checkpoint", segment.id)
                return segment

        if self.retriever is not None:
            context = self.retriever.build(segment.source_text)
            segment.tm_examples = context.get("tm_examples", [])
            segment.glossary_hits = context.get("glossary_hits", [])
            # Точное совпадение в памяти переводов: берём готовое, LLM не нужен.
            exact = context.get("exact_match")
            if exact:
                segment.translation = exact
                segment.issues.append(
                    QAIssue("tm_exact", "reused exact translation-memory match", "info")
                )
                logger.debug("segment %s: exact TM hit (%d chars)",
                             segment.id, len(segment.source_text))
                return segment

        messages = self._build_messages(segment, source_context)
        raw = self._chat(messages)
        segment.attempts = 1
        self._finalize(segment, raw)

        # Цикл самопочинки: список проблем от валидаторов уходит модели
        # вместе с её же прошлым ответом — пока не ок или не кончились
        # попытки. Сетевые паузы здесь не нужны: перегрузку провайдера
        # обрабатывает клиент (ретраи с бэкоффом, общий кулдаун на 429),
        # а починка по замечаниям валидатора — это новый осмысленный
        # запрос, спать перед ним вслепую значит терять секунды на
        # каждом проблемном сегменте.
        system = messages[0]["content"]
        while (
            not segment.ok and segment.attempts <= cfg.max_repair_attempts
        ):
            logger.info(
                "Segment %s: repair attempt %d (%s)",
                segment.id, segment.attempts,
                "; ".join(i.code for i in segment.issues),
            )
            issues_text = "\n".join(f"- {i.message}" for i in segment.issues)
            repair = REPAIR_USER.format(
                issues=issues_text,
                source=segment.masked_text,
                translation=raw,
            )
            new_raw = self._chat([
                {"role": "system", "content": system},
                {"role": "user", "content": repair},
            ])
            segment.attempts += 1
            # Модель «упёрлась»: дословно повторила прошлый ответ — новые
            # попытки дадут то же самое, не жжём вызовы впустую.
            stuck = new_raw.strip() == raw.strip()
            raw = new_raw
            self._finalize(segment, raw)
            if stuck and not segment.ok:
                logger.info("Segment %s: model repeated the same answer, "
                            "stopping repair early", segment.id)
                break

        # Последний шанс: цикл починки не спас плейсхолдеры (мелкие модели
        # ломаются об сами токены ⟦PH…⟧) — пробуем перевод БЕЗ маскировки.
        # Иначе final_text() откатится на оригинал и в документ уедет
        # непереведённый кусок.
        if (
            not segment.ok
            and cfg.unmasked_rescue
            and segment.placeholders
            and any(i.code in ("placeholder_missing", "placeholder_unknown")
                    for i in segment.issues)
        ):
            self._unmasked_rescue(segment, source_context)

        logger.debug(
            "segment %s: %d chars, %d placeholders, %d attempt(s), "
            "%d issue(s), %.1fs%s",
            segment.id, len(segment.source_text), len(segment.placeholders),
            segment.attempts, len(segment.issues),
            _time.monotonic() - started, "" if segment.ok else " [FAILED]",
        )
        # В кэш и чекпойнт — только чистые сегменты: возобновлённый прогон
        # должен перепробовать проблемные, а не закэшировать мусор.
        if segment.ok and segment.translation:
            self._run_cache.put(segment.source_text, segment.translation)
            if self.checkpoint is not None:
                self.checkpoint.put(segment.source_text, segment.translation)
        return segment

    def _unmasked_rescue(self, segment: Segment, source_context: str) -> None:
        """Перевести сегмент без плейсхолдеров — спасение, когда модель
        упорно теряет/корёжит токены ⟦PH…⟧.

        Формулы/ссылки/код отправляются как есть, с инструкцией сохранить
        их байт-в-байт. Результат принимается только если содержимое
        КАЖДОГО плейсхолдера дословно присутствует в ответе (сравнение с
        нормализацией пробелов) и валидаторы не нашли ошибок — иначе
        сегмент остаётся как был (и final_text откатится на оригинал)."""
        try:
            system = build_translation_system(
                self.config.source_lang, self.config.target_lang,
                doc_summary=self.doc_summary or None,
            )
            user = build_user_message(segment.source_text, source_context or None)
            user += (
                "\n\nIMPORTANT: copy every formula, LaTeX command, URL, "
                "inline code and citation marker EXACTLY as in the source, "
                "byte for byte. Translate only the natural-language text."
            )
            raw = self._chat([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])
        except Exception as exc:  # noqa: BLE001 - спасение не должно ронять сегмент
            logger.warning("unmasked rescue failed for %s: %s", segment.id, exc)
            return
        segment.attempts += 1
        rescue = _strip_wrapping_fence(raw.strip())
        if not rescue.strip():
            return
        # Всё замаскированное содержимое обязано пережить перевод дословно.
        for original in segment.placeholders.values():
            if not _contains_normalized(rescue, original):
                logger.info("segment %s: unmasked rescue dropped protected "
                            "content, keeping the failed attempt", segment.id)
                return
        # Валидируем как обычный перевод, но без плейсхолдерной механики.
        candidate = Segment(
            id=segment.id, kind="translate",
            source_text=segment.source_text,
            masked_text=segment.source_text,
            translation=rescue,
        )
        issues = validate_segment(candidate, self.config)
        if any(i.severity == "error" for i in issues):
            logger.info("segment %s: unmasked rescue failed validation (%s)",
                        segment.id, "; ".join(i.code for i in issues))
            return
        segment.translation = rescue
        segment.issues = issues
        segment.issues.append(QAIssue(
            "unmasked_rescue",
            "placeholders kept breaking; re-translated without masking, "
            "protected content verified verbatim", "info",
        ))
        logger.info("segment %s: rescued by unmasked re-translation", segment.id)

    def _finalize(self, segment: Segment, raw_translation: str) -> None:
        """Восстановить плейсхолдеры, провалидировать и сохранить попытку."""
        raw_translation = _strip_wrapping_fence(raw_translation.strip())
        restored, missing, unknown = unmask(raw_translation, segment.placeholders)
        segment.issues = []
        if missing:
            segment.issues.append(
                QAIssue(
                    "placeholder_missing",
                    f"placeholders lost in translation: {', '.join(missing[:10])}",
                    "error",
                )
            )
        if unknown:
            segment.issues.append(
                QAIssue(
                    "placeholder_unknown",
                    f"invented placeholder tokens: {', '.join(unknown[:10])}",
                    "error",
                )
            )
        segment.translation = restored
        segment.issues.extend(validate_segment(segment, self.config))

    # -- доперевод остатков ---------------------------------------------
    def retranslate_residual(self, segments: list[Segment]) -> int:
        """Доперевести сегменты, которые валидатор всё ещё находит на
        исходном языке (модель оставила целый кусок непереведённым).

        Свежая попытка с усиленной инструкцией; принимаем результат
        только если исходного языка стало меньше (иначе оставляем как
        было — хуже не сделаем). Возвращает число реально исправленных."""
        fixed = 0
        for segment in segments:
            if segment.kind != "translate" or not segment.translation:
                continue
            if not any(i.code == "untranslated" for i in segment.issues):
                continue
            before = residual_source_ratio(
                segment.translation, self.config.source_lang, self.config.target_lang)
            prev_translation = segment.translation
            prev_issues = list(segment.issues)
            try:
                system = build_translation_system(
                    self.config.source_lang, self.config.target_lang,
                    doc_summary=self.doc_summary or None,
                )
                user = (
                    "Предыдущий перевод оставил часть текста на исходном языке. "
                    "Переведи АБСОЛЮТНО ВЕСЬ текст ниже на язык назначения, "
                    "не оставляя ни одного слова на языке оригинала. Сохрани "
                    "плейсхолдеры ⟦PH…⟧ и структуру.\n\n" + segment.masked_text
                )
                raw = self._chat([
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ])
            except Exception as exc:
                logger.warning("residual re-translation failed for %s: %s",
                               segment.id, exc)
                continue
            self._finalize(segment, raw)
            after = residual_source_ratio(
                segment.translation, self.config.source_lang, self.config.target_lang)
            # Критерий приёма: остаток исходного языка ниже порога (флаг
            # untranslated снят, а не просто «стало меньше») и плейсхолдеры
            # целы. Именно целостность плейсхолдеров, а не полный
            # ``segment.ok``: прочие ошибки (например, длина) не мешают
            # отгрузить перевод, а требование полного ok отбраковывало
            # почти всё, делая стадию бесполезной.
            new_ph = any(
                i.code in ("placeholder_missing", "placeholder_unknown")
                and i.severity == "error"
                for i in segment.issues
            )
            still_untranslated = any(
                i.code == "untranslated" for i in segment.issues
            )
            if (
                after < before
                and not new_ph
                and not still_untranslated
                and segment.translation
                and segment.translation.strip()
            ):
                segment.attempts += 1
                fixed += 1
            else:
                segment.translation = prev_translation
                segment.issues = prev_issues
        return fixed

    # -- весь документ --------------------------------------------------
    def translate_segments(
        self,
        segments: list[Segment],
        progress: Optional[ProgressCb] = None,
        should_pause: Optional[Callable[[], bool]] = None,
        on_batch: Optional[Callable[[int, int], None]] = None,
    ) -> tuple[list[Segment], bool]:
        """Перевести все сегменты вида "translate" на месте.

        Возвращает ``(segments, paused)``. ``should_pause`` опрашивается
        перед стартом каждой партии и после каждого завершённого
        сегмента; как только он вернул True, новые сегменты не стартуют
        — начатые довершаются, чтобы их работа не пропала — и метод
        возвращает ``paused=True``. Нетронутые сегменты остаются
        ``translation=None``, их подхватит чекпойнт при возобновлении.

        Сегменты обрабатываются партиями по ``translate_batch_size``
        через ОДИН общий ``ThreadPoolExecutor`` на весь документ (в
        v0.18 пул больше не пересоздаётся на каждую партию); партия
        ограничивает число одновременно поставленных запросов, чтобы
        огромный документ не держал тысячи задач разом.
        ``on_batch(done, total)`` срабатывает после каждой партии —
        пайплайн пишет частичный документ на диск и перепроверяет
        свободную память, прежде чем продолжить.
        """
        # Свежий кэш повторов на каждый документ: не тащим переводы из
        # прошлых задач этого же экземпляра и не копим память бесконечно.
        self._run_cache = _RunCache()

        # Контекст с исходной стороны (безопасно для параллели): хвост
        # предыдущего сегмента-источника разглаживает швы между кусками.
        ctx_chars = self.config.source_context_chars
        contexts: dict[str, str] = {}
        prev_source = ""
        for segment in segments:
            if segment.kind == "translate" and ctx_chars > 0 and prev_source:
                contexts[segment.id] = prev_source[-ctx_chars:]
            if segment.source_text.strip():
                prev_source = segment.source_text

        to_translate = [s for s in segments if s.kind == "translate"]
        total = len(to_translate)
        done = 0
        # 0/отрицательное = «одна партия на весь документ»
        batch_size = self.config.translate_batch_size
        if batch_size is None or batch_size <= 0:
            batch_size = total or 1

        workers = max(1, self.config.max_workers)
        if workers == 1 or total <= 1:
            return self._translate_sequential(
                segments, to_translate, contexts, total, batch_size,
                progress, should_pause, on_batch,
            )

        paused = False
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="translate"
        ) as pool:
            for batch_start in range(0, total, batch_size):
                # Пауза, пришедшая между партиями, замечается ДО отправки
                # новых запросов — партия даже не стартует.
                if not paused and should_pause and should_pause():
                    paused = True
                if paused:
                    for pending in to_translate[batch_start:]:
                        if pending.translation is None:
                            pending.issues.append(_paused_issue())
                    break

                batch = to_translate[batch_start:batch_start + batch_size]
                futures = {
                    pool.submit(
                        self.translate_segment, seg, contexts.get(seg.id, "")
                    ): seg
                    for seg in batch
                }
                for future in as_completed(futures):
                    segment = futures[future]
                    try:
                        future.result()
                    except CancelledError:
                        # пауза случилась до старта этого сегмента; оставляем
                        # его резюму — и не засчитываем в прогресс, иначе
                        # замороженный бар покажет несделанную работу
                        segment.issues.append(_paused_issue())
                        continue
                    except Exception as exc:
                        # Взорвавшийся сегмент (отвал провайдера) не должен
                        # утопить весь документ: помечаем и идём дальше —
                        # готовые сегменты всё равно попадут в чекпойнт.
                        logger.error("Segment %s failed: %s", segment.id, exc)
                        segment.issues.append(
                            QAIssue("exception", f"translation call failed: {exc}", "error")
                        )
                    done += 1
                    if progress:
                        progress(done, total, segment.id)
                    if not paused and should_pause and should_pause():
                        paused = True
                        cancelled = sum(1 for f in futures if f.cancel())
                        logger.info(
                            "translation paused: %d/%d done, %d not-yet-started "
                            "segment(s) cancelled, waiting for in-flight to finish",
                            done, total, cancelled,
                        )
                if on_batch:
                    on_batch(done, total)
                if paused:
                    # более поздние партии даже не отправлялись — пометим и их
                    for pending in to_translate[batch_start + len(batch):]:
                        if pending.translation is None:
                            pending.issues.append(_paused_issue())
                    break
        return segments, paused

    def _translate_sequential(
        self,
        segments: list[Segment],
        to_translate: list[Segment],
        contexts: dict[str, str],
        total: int,
        batch_size: int,
        progress: Optional[ProgressCb],
        should_pause: Optional[Callable[[], bool]],
        on_batch: Optional[Callable[[int, int], None]],
    ) -> tuple[list[Segment], bool]:
        """Последовательный путь (``max_workers == 1``): без пула, с теми
        же семантиками паузы, прогресса и партий."""
        done = 0
        for idx, segment in enumerate(to_translate):
            if should_pause and should_pause():
                for pending in to_translate[idx:]:
                    pending.issues.append(_paused_issue())
                logger.info(
                    "translation paused: %d/%d segment(s) done", done, total
                )
                if on_batch:
                    on_batch(done, total)
                return segments, True
            # Взорвавшийся сегмент (отвал провайдера) не должен утопить
            # весь документ: помечаем и идём дальше, как в параллельном
            # пути — готовые сегменты всё равно попадут в чекпойнт.
            try:
                self.translate_segment(segment, contexts.get(segment.id, ""))
            except Exception as exc:
                logger.error("Segment %s failed: %s", segment.id, exc)
                segment.issues.append(
                    QAIssue("exception", f"translation call failed: {exc}", "error")
                )
            done += 1
            if progress:
                progress(done, total, segment.id)
            if on_batch and done % batch_size == 0:
                on_batch(done, total)
        if on_batch and done % batch_size != 0:
            on_batch(done, total)
        return segments, False


def _paused_issue() -> QAIssue:
    """Метка сегмента, не переведённого из-за паузы — предупреждение,
    не ошибка: его не пробовали, значит он не «провалился». Возобновление
    задачи дойдёт до него через чекпойнт."""
    return QAIssue(
        "paused", "translation paused before this segment was reached; "
        "resume the job to continue", "warning",
    )


def _contains_normalized(haystack: str, needle: str) -> bool:
    """Вхождение с нормализацией пробелов: перенос строки внутри формулы
    или двойной пробел не должны заваливать проверку сохранности."""
    norm = lambda s: " ".join(s.split())  # noqa: E731 - локальный хелпер
    return norm(needle) in norm(haystack)


def _strip_wrapping_fence(text: str) -> str:
    """Модели иногда заворачивают весь ответ в ```-ограду — снимаем её."""
    if text.startswith("```") and text.endswith("```") and len(text) >= 6:
        body = text[3:-3]
        # первая строка может быть языковым тегом (```markdown) — убираем
        first_nl = body.find("\n")
        if first_nl != -1 and " " not in body[:first_nl].strip():
            body = body[first_nl + 1:]
        return body.strip()
    return text
