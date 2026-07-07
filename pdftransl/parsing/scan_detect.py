"""Detect PDFs a text extractor can't handle: scans and garbled fonts.

Two failure modes need OCR instead of text extraction:

- **Scanned / image-only** pages carry no text layer at all — a text
  extractor yields an empty document.
- **Garbled text layer** — the page renders fine but the embedded
  fonts have no ToUnicode map, so extraction returns "кракозябры"
  (Private Use Area glyphs, replacement chars, mojibake). Common in
  Cyrillic scientific PDFs (Cyberleninka & co.). Formally there *is*
  text, so scan detection alone misses it.

Both are surfaced here so the pipeline can route to OCR. Detection
uses PyMuPDF; without it we conservatively report "fine" so nothing
breaks.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pdftransl.parsing.text_quality import text_quality

logger = logging.getLogger(__name__)


def scan_stats(pdf_path: str | Path) -> dict:
    """Return per-document extraction stats.

    Keys: ``pages``, ``text_chars``, ``chars_per_page``,
    ``image_pages``, ``is_scanned``, ``is_garbled``,
    ``garbled_ratio``, ``needs_ocr`` (scanned OR garbled).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {"pages": 0, "is_scanned": False, "is_garbled": False,
                "needs_ocr": False, "reason": "pymupdf-missing"}

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:  # pragma: no cover - corrupt file
        logger.warning("scan detection could not open %s: %s", pdf_path, exc)
        return {"pages": 0, "is_scanned": False, "is_garbled": False,
                "needs_ocr": False, "reason": "open-failed"}

    pages = doc.page_count
    text_chars = 0
    image_pages = 0
    text_parts: list[str] = []
    for page in doc:
        text = page.get_text("text").strip()
        text_chars += len(text)
        if len(text_parts) < 10:  # sample the head for the quality check
            text_parts.append(text)
        page_area = float(page.rect.width * page.rect.height) or 1.0
        image_area = 0.0
        for img in page.get_images(full=True):
            try:
                for rect in page.get_image_rects(img[0]):
                    image_area += rect.width * rect.height
            except Exception:
                continue
        if len(text) < 50 and image_area / page_area > 0.5:
            image_pages += 1
    doc.close()

    chars_per_page = text_chars / pages if pages else 0
    is_scanned = bool(
        pages > 0
        and (image_pages / pages >= 0.5)
        and chars_per_page < 100
    )
    quality = text_quality("\n".join(text_parts))
    is_garbled = quality["is_garbled"]
    return {
        "pages": pages,
        "text_chars": text_chars,
        "chars_per_page": round(chars_per_page, 1),
        "image_pages": image_pages,
        "is_scanned": is_scanned,
        "is_garbled": is_garbled,
        "garbled_ratio": quality["garbled_ratio"],
        "needs_ocr": bool(is_scanned or is_garbled),
    }


def is_scanned(pdf_path: str | Path) -> bool:
    return scan_stats(pdf_path).get("is_scanned", False)
