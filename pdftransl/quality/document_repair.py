"""LLM-починка вёрстки итогового документа (артефакты парсера).

Парсеры (MinerU, OCR) иногда рвут абзац посреди предложения, путают
уровни заголовков или переставляют куски двухколоночной вёрстки. Эта
стадия просит LLM починить ТОЛЬКО структуру, не трогая содержание,
формулы и таблицы — и принимает результат лишь если контент цел
(строгая проверка на потерю текста и формул). Без починки хуже не
делаем: любой сомнительный кусок остаётся как был.

Дорого (ещё один проход LLM по всему документу), поэтому по умолчанию
выключено (``fix_layout``).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pdftransl.llm.base import BaseLLMClient

if TYPE_CHECKING:  # pragma: no cover
    from pdftransl.config import PipelineConfig

logger = logging.getLogger(__name__)

_SYSTEM = """\
Ты — корректор ВЁРСТКИ переведённого научного документа в Markdown.
Твоя задача — починить артефакты парсера, НЕ меняя смысла и содержания.

МОЖНО:
- склеить абзац, разорванный посреди предложения на несколько кусков;
- исправить уровень заголовка (#, ##, ###), если он явно неверный;
- восстановить очевидный порядок чтения переставленных кусков;
- убрать случайные разрывы строк внутри предложения.

НЕЛЬЗЯ:
- переводить, перефразировать, сокращать или дополнять текст;
- трогать формулы ($...$, $$...$$), таблицы, код и ссылки — копируй их
  ДОСЛОВНО, символ в символ;
- удалять или добавлять содержание.

Верни ТОЛЬКО исправленный Markdown, без комментариев и без ```-ограды."""

_FORMULA_RE = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$", re.DOTALL)
_LETTERS_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def _letters(text: str) -> int:
    return len(_LETTERS_RE.findall(text))


def _formula_count(text: str) -> int:
    return len(_FORMULA_RE.findall(text))


def _split_chunks(markdown: str, budget: int) -> list[str]:
    """Режем документ на куски до ``budget`` символов по границам пустых
    строк (границам блоков) — заголовок/таблица/формула не разрываются."""
    blocks = re.split(r"\n\s*\n", markdown)
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for block in blocks:
        if cur and cur_len + len(block) > budget:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0
        cur.append(block)
        cur_len += len(block) + 2
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def _content_preserved(before: str, after: str) -> bool:
    """Контент цел: не потеряно больше ~12% букв и число формул то же.

    Ловит галлюцинацию/обрезание — LLM, которой велено чинить вёрстку,
    иногда всё равно выкидывает или переписывает текст."""
    lb, la = _letters(before), _letters(after)
    if lb == 0:
        return la == 0
    if la < lb * 0.88 or la > lb * 1.12:
        return False
    return _formula_count(before) == _formula_count(after)


def repair_layout(
    markdown: str, client: BaseLLMClient, config: "PipelineConfig",
) -> tuple[str, int]:
    """Починить вёрстку документа по кускам. Возвращает ``(md, n_fixed)``.

    Каждый кусок чинится независимо; результат принимается только если
    контент цел (иначе оставляем оригинальный кусок). Ошибка LLM на
    куске не фатальна — кусок остаётся как был.
    """
    chunks = _split_chunks(markdown, max(config.chunk_char_budget, 1500))
    out: list[str] = []
    fixed = 0
    for i, chunk in enumerate(chunks):
        if _letters(chunk) < 40:      # мелочь (одинокая формула/таблица) не трогаем
            out.append(chunk)
            continue
        try:
            raw = client.chat(
                [{"role": "system", "content": _SYSTEM},
                 {"role": "user", "content": chunk}],
                temperature=0.0,
                max_tokens=config.max_output_tokens,
            ).strip()
        except Exception as exc:  # noqa: BLE001 - починка сугубо best-effort
            logger.warning("layout repair failed on chunk %d, keeping original: %s",
                           i + 1, exc)
            out.append(chunk)
            continue
        raw = _strip_fence(raw)
        if raw and raw != chunk and _content_preserved(chunk, raw):
            out.append(raw)
            fixed += 1
        else:
            out.append(chunk)
    return "\n\n".join(out), fixed


def _strip_fence(text: str) -> str:
    if text.startswith("```") and text.endswith("```"):
        body = text[3:-3]
        nl = body.find("\n")
        if nl != -1 and " " not in body[:nl].strip():
            body = body[nl + 1:]
        return body.strip()
    return text
