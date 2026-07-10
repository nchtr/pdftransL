"""Конвертация HTML-таблиц в Markdown-таблицы.

Зачем: парсеры (MinerU, VLM-OCR) часто отдают таблицы сырым HTML
(`<table><tr><td>…`). Дальше по пайплайну это беда дважды:

* блочный HTML сплиттер помечает непереводимым — содержимое таблицы
  молча остаётся на языке оригинала;
* «рваные» инлайновые куски (`</td><td>` посреди абзаца — типично для
  OCR) уходят в LLM как текст, и мелкая модель давится тегами,
  выдавая макароническую кашу с протёкшими `</tr><tr>`.

Markdown-таблица же и переводится (построчный валидатор её стережёт),
и корректно рендерится в HTML/DOCX/PDF при экспорте.

Конвертер сознательно простой: незакрытые/вложенные-экзотические
таблицы, rowspan/colspan сплющиваются по ячейкам; если разобрать ряд
не удалось — исходный текст остаётся нетронутым (хуже не делаем).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Последовательность рядов, опционально обёрнутая в <table>/<tbody>.
# Ловим и таблицу целиком, и голые ряды без обёртки (OCR-огрызки).
_TABLE_RE = re.compile(
    r"(?:<table[^>]*>\s*)?(?:<t(?:head|body)[^>]*>\s*)?"
    r"(?:<tr[^>]*>.*?</tr>\s*)+"
    r"(?:</t(?:head|body)>\s*)?(?:</table>)?",
    re.IGNORECASE | re.DOTALL,
)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]*>")  # прочие теги внутри ячейки
_WS_RE = re.compile(r"\s+")


def _cell_text(html: str) -> str:
    """Текст ячейки: без вложенных тегов, с экранированной вертикальной
    чертой (она — синтаксис Markdown-таблицы)."""
    text = _TAG_RE.sub(" ", html)
    text = _WS_RE.sub(" ", text).strip()
    return text.replace("|", r"\|")


def _table_to_markdown(html: str) -> str | None:
    """Один HTML-кусок с рядами -> Markdown-таблица (None = не разобрали)."""
    rows: list[list[str]] = []
    for row_match in _ROW_RE.finditer(html):
        cells = [_cell_text(c) for c in _CELL_RE.findall(row_match.group(1))]
        if cells:
            rows.append(cells)
    if not rows:
        return None
    width = max(len(r) for r in rows)
    if width == 0:
        return None
    lines = []
    for i, row in enumerate(rows):
        row = row + [""] * (width - len(row))  # ряды разной ширины — добиваем
        lines.append("| " + " | ".join(row) + " |")
        if i == 0:
            # первый ряд считаем шапкой: Markdown требует разделитель
            lines.append("|" + "---|" * width)
    return "\n".join(lines)


def convert_html_tables(text: str) -> str:
    """Заменить все HTML-таблицы (и голые последовательности ``<tr>``)
    в тексте на Markdown-таблицы. Неразбираемые куски не трогаются."""
    if "<tr" not in text.lower():
        return text  # быстрый выход: таблиц нет

    converted = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal converted
        md = _table_to_markdown(match.group(0))
        if md is None:
            return match.group(0)
        converted += 1
        # пустые строки вокруг — чтобы сплиттер увидел отдельный блок
        return f"\n\n{md}\n\n"

    result = _TABLE_RE.sub(repl, text)
    if converted:
        logger.debug("converted %d HTML table(s) to markdown", converted)
    return result
