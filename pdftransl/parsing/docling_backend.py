"""Docling (IBM) — парсер с сильными таблицами.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pdftransl.exceptions import ParserError
from pdftransl.models import Asset, ParsedDocument
from pdftransl.parsing.base import ParserBackend

logger = logging.getLogger(__name__)


class DoclingBackend(ParserBackend):
    name = "docling"

    def available(self) -> bool:
        try:
            import docling  # noqa: F401
            return True
        except ImportError:
            return False

    def parse(self, pdf_path: str | Path, workdir: str | Path) -> ParsedDocument:
        from docling.document_converter import DocumentConverter

        pdf_path = Path(pdf_path)
        workdir = Path(workdir)
        images_dir = workdir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        if not pdf_path.exists():
            raise ParserError(f"PDF not found: {pdf_path}")

        logger.info("Docling: converting %s", pdf_path.name)
        result = DocumentConverter().convert(str(pdf_path))
        document = result.document
        markdown = document.export_to_markdown()

        assets: list[Asset] = []
        # picture extraction is best-effort: API varies across versions
        try:
            for i, picture in enumerate(getattr(document, "pictures", []) or []):
                image = getattr(picture, "image", None)
                pil = getattr(image, "pil_image", None) if image else None
                if pil is None:
                    continue
                rel = f"images/figure_{i + 1}.png"
                out = images_dir / f"figure_{i + 1}.png"
                pil.save(out)
                assets.append(Asset(path=str(out), rel_path=rel, kind="image"))
        except Exception as exc:  # pragma: no cover - version drift
            logger.warning("Docling image export failed: %s", exc)

        md_path = workdir / f"{pdf_path.stem}.md"
        md_path.write_text(markdown, encoding="utf-8")
        return ParsedDocument(
            source_path=str(pdf_path),
            markdown=markdown,
            markdown_path=str(md_path),
            assets=assets,
            backend=self.name,
        )
