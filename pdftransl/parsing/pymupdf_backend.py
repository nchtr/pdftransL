"""PyMuPDF — мгновенный текстовый фолбэк.

Без распознавания формул и сканов; для «текстовых» статей достаточно,
для остального — последний рубеж, когда ничего лучше не установлено.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pdftransl.exceptions import ParserError, ParserUnavailableError
from pdftransl.models import Asset, ParsedDocument
from pdftransl.parsing.base import ParserBackend

logger = logging.getLogger(__name__)


class PyMuPdfBackend(ParserBackend):
    name = "pymupdf"

    def available(self) -> bool:
        try:
            import fitz  # noqa: F401  (PyMuPDF)
            return True
        except ImportError:
            return False

    def parse(self, pdf_path: str | Path, workdir: str | Path) -> ParsedDocument:
        try:
            import fitz
        except ImportError as exc:
            raise ParserUnavailableError(
                "PyMuPDF is not installed (`pip install PyMuPDF`)."
            ) from exc

        pdf_path = Path(pdf_path)
        workdir = Path(workdir)
        images_dir = workdir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        if not pdf_path.exists():
            raise ParserError(f"PDF not found: {pdf_path}")

        md_parts: list[str] = []
        assets: list[Asset] = []
        seen_xrefs: set[int] = set()

        doc = fitz.open(pdf_path)
        try:
            for page_no, page in enumerate(doc, start=1):
                text = page.get_text("text").strip()
                if text:
                    md_parts.append(text)
                for img in page.get_images(full=True):
                    xref = img[0]
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)
                    try:
                        info = doc.extract_image(xref)
                    except Exception:  # pragma: no cover - malformed image
                        continue
                    ext = info.get("ext", "png")
                    name = f"page{page_no}_img{xref}.{ext}"
                    out = images_dir / name
                    out.write_bytes(info["image"])
                    rel = f"images/{name}"
                    md_parts.append(f"![figure page {page_no}]({rel})")
                    assets.append(
                        Asset(path=str(out), rel_path=rel, kind="image", page=page_no)
                    )
        finally:
            doc.close()

        markdown = "\n\n".join(md_parts) + "\n"
        md_path = workdir / f"{pdf_path.stem}.md"
        md_path.write_text(markdown, encoding="utf-8")
        logger.info("PyMuPDF parsed %s: %d chars, %d images",
                    pdf_path.name, len(markdown), len(assets))
        return ParsedDocument(
            source_path=str(pdf_path),
            markdown=markdown,
            markdown_path=str(md_path),
            assets=assets,
            backend=self.name,
            meta={"warning": "pymupdf fallback: formulas are not converted to LaTeX"},
        )
