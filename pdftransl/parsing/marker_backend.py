"""marker — быстрый парсер PDF с поддержкой LaTeX-формул.

Легче MinerU, качество формул ниже, скорость выше.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pdftransl.exceptions import ParserError
from pdftransl.models import Asset, ParsedDocument
from pdftransl.parsing.base import ParserBackend

logger = logging.getLogger(__name__)


class MarkerBackend(ParserBackend):
    name = "marker"
    _converter = None  # model weights load once per process

    def available(self) -> bool:
        try:
            import marker  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_converter(self):
        if MarkerBackend._converter is None:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict

            logger.info("marker: loading models (first run only)")
            MarkerBackend._converter = PdfConverter(artifact_dict=create_model_dict())
        return MarkerBackend._converter

    def parse(self, pdf_path: str | Path, workdir: str | Path) -> ParsedDocument:
        from marker.output import text_from_rendered

        pdf_path = Path(pdf_path)
        workdir = Path(workdir)
        images_dir = workdir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        if not pdf_path.exists():
            raise ParserError(f"PDF not found: {pdf_path}")

        logger.info("marker: converting %s", pdf_path.name)
        rendered = self._get_converter()(str(pdf_path))
        markdown, _meta, images = text_from_rendered(rendered)

        assets: list[Asset] = []
        for name, pil in (images or {}).items():
            safe = Path(name).name
            out = images_dir / safe
            try:
                pil.save(out)
            except Exception as exc:  # pragma: no cover
                logger.warning("marker image save failed for %s: %s", name, exc)
                continue
            rel = f"images/{safe}"
            # marker references images by their original name in the md
            markdown = markdown.replace(f"]({name})", f"]({rel})")
            assets.append(Asset(path=str(out), rel_path=rel, kind="image"))

        md_path = workdir / f"{pdf_path.stem}.md"
        md_path.write_text(markdown, encoding="utf-8")
        return ParsedDocument(
            source_path=str(pdf_path),
            markdown=markdown,
            markdown_path=str(md_path),
            assets=assets,
            backend=self.name,
        )
