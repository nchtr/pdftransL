"""Detect scanned / image-only PDFs.

A scanned page carries no extractable text layer — just a full-page
image. Feeding such a file to a text extractor (PyMuPDF) silently
yields an empty document, so the pipeline needs to notice this and
route to OCR instead. Detection uses PyMuPDF; when it is unavailable
we conservatively report "not scanned" so nothing breaks.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def scan_stats(pdf_path: str | Path) -> dict:
    """Return per-document scan statistics.

    Keys: ``pages``, ``text_chars``, ``chars_per_page``,
    ``image_pages`` (pages whose text is negligible while an image
    covers most of the page), ``is_scanned``.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {"pages": 0, "is_scanned": False, "reason": "pymupdf-missing"}

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:  # pragma: no cover - corrupt file
        logger.warning("scan detection could not open %s: %s", pdf_path, exc)
        return {"pages": 0, "is_scanned": False, "reason": "open-failed"}

    pages = doc.page_count
    text_chars = 0
    image_pages = 0
    for page in doc:
        text = page.get_text("text").strip()
        text_chars += len(text)
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
    # Scanned if most pages are image-with-no-text, or the whole document
    # has almost no extractable text while containing page-sized images.
    is_scanned = bool(
        pages > 0
        and (image_pages / pages >= 0.5)
        and chars_per_page < 100
    )
    return {
        "pages": pages,
        "text_chars": text_chars,
        "chars_per_page": round(chars_per_page, 1),
        "image_pages": image_pages,
        "is_scanned": is_scanned,
    }


def is_scanned(pdf_path: str | Path) -> bool:
    return scan_stats(pdf_path).get("is_scanned", False)
