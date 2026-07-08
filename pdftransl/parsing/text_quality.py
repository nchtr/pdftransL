"""Детектор «кракозябр» — битого извлечения текста.

Некоторые PDF (классика — кириллические сканы с Cyberleninka) несут
текстовый слой, чьи шрифты не имеют вменяемой ToUnicode-карты.
Страница *выглядит* нормально (глифы рисуются), но экстрактор
вытаскивает мусор: символы Private Use Area, U+FFFD, mojibake.
Скормить это LLM — получить ровно тот самый симптом «долго думает и
выдаёт кракозябры»: модель прилежно "переводит" шум.

Это не то же самое, что скан (страницы-картинки): текст тут есть,
просто неправильный. Лечение одно — OCR отрендеренных страниц, поэтому
пайплайн уводит в OCR и такие документы. Здесь же —
несовпадение языка/письменности (перепутанное направление перевода).
"""

from __future__ import annotations

import re
import unicodedata

# UTF-8 bytes of Cyrillic decoded as latin-1 start with Ð (0xD0) / Ñ (0xD1);
# a pile of these is the tell-tale of that specific mojibake.
_MOJIBAKE_LEAD = re.compile(r"[ÐÑ][-¿]")


def _is_suspicious(ch: str) -> bool:
    code = ord(ch)
    # Private Use Areas — where broken CID fonts dump unmapped glyphs
    if 0xE000 <= code <= 0xF8FF or 0xF0000 <= code <= 0x10FFFD:
        return True
    if ch == "�":  # replacement character
        return True
    # "not assigned" / control (excluding normal whitespace handled by caller)
    cat = unicodedata.category(ch)
    return cat in ("Co", "Cn")


def garbled_ratio(text: str) -> float:
    """Fraction of non-space characters that look like broken extraction."""
    chars = [c for c in text if not c.isspace()]
    if len(chars) < 40:
        return 0.0
    suspicious = sum(1 for c in chars if _is_suspicious(c))
    return suspicious / len(chars)


def letter_ratio(text: str) -> float:
    """Fraction of non-space characters that are letters of any script."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    letters = sum(1 for c in chars if unicodedata.category(c).startswith("L"))
    return letters / len(chars)


def meaningful_ratio(text: str) -> float:
    """Fraction of non-space characters that are letters OR digits.

    Real prose and numeric tables both score high; a page of filler
    glyphs (·····, □□□, replacement chars) scores near zero."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    good = sum(
        1 for c in chars
        if unicodedata.category(c).startswith("L") or c.isdigit()
    )
    return good / len(chars)


def text_quality(text: str) -> dict:
    """Assess extracted text; returns ratios and an ``is_garbled`` verdict."""
    gr = garbled_ratio(text)
    lr = letter_ratio(text)
    mr = meaningful_ratio(text)
    mojibake_hits = len(_MOJIBAKE_LEAD.findall(text))
    stripped = "".join(text.split())
    mojibake_ratio = mojibake_hits / max(len(stripped), 1)
    # Garbled if a meaningful share of glyphs are PUA/replacement, or the
    # latin1-mojibake signature is dense, or almost nothing reads as
    # letters/digits (a wall of filler glyphs).
    is_garbled = bool(
        len(stripped) >= 200
        and (
            gr >= 0.12
            or mojibake_ratio >= 0.10
            or (lr < 0.35 and gr > 0.02)
            or mr < 0.20
        )
    )
    return {
        "garbled_ratio": round(gr, 3),
        "letter_ratio": round(lr, 3),
        "meaningful_ratio": round(mr, 3),
        "mojibake_ratio": round(mojibake_ratio, 3),
        "is_garbled": is_garbled,
    }


def is_garbled(text: str) -> bool:
    return text_quality(text)["is_garbled"]


_SCRIPT_PATTERNS = {
    "cyrillic": re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]"),
    "latin": re.compile(r"[A-Za-z]"),
    "cjk": re.compile(r"[一-鿿぀-ヿ]"),
    "greek": re.compile(r"[Ͱ-Ͽ]"),
    "arabic": re.compile(r"[؀-ۿ]"),
}
# language code -> expected dominant script
LANG_SCRIPT = {
    "ru": "cyrillic", "uk": "cyrillic", "kk": "cyrillic", "be": "cyrillic",
    "en": "latin", "de": "latin", "fr": "latin", "es": "latin", "it": "latin",
    "zh": "cjk", "ja": "cjk", "el": "greek", "ar": "arabic",
}


def dominant_script(text: str) -> tuple[str | None, float]:
    """Return the most common script in ``text`` and its share of letters."""
    counts = {name: len(pat.findall(text)) for name, pat in _SCRIPT_PATTERNS.items()}
    total = sum(counts.values())
    if total < 30:
        return None, 0.0
    name = max(counts, key=counts.get)
    return name, counts[name] / total


def language_mismatch(text: str, source_lang: str) -> str | None:
    """If the text's dominant script clearly isn't the declared source
    language's script, return the detected script (a likely wrong
    translation direction); otherwise None."""
    expected = LANG_SCRIPT.get(source_lang.lower())
    if not expected:
        return None
    script, share = dominant_script(text)
    if script and script != expected and share >= 0.6:
        return script
    return None
