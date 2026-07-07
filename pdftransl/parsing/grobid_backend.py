"""GROBID backend — precise bibliography and structure extraction.

GROBID (https://github.com/kermitt2/grobid) is a service that turns a
scholarly PDF into structured TEI-XML with excellent header, section
and reference segmentation. It doesn't recognize formulas to LaTeX, so
it's best for text-heavy papers or as a fallback. Point
``GROBID_URL`` at a running server (default http://localhost:8070).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import ParserError
from pdftransl.models import ParsedDocument
from pdftransl.parsing.base import ParserBackend

logger = logging.getLogger(__name__)

_TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


class GrobidBackend(ParserBackend):
    name = "grobid"

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config
        self.url = os.environ.get("GROBID_URL", "http://localhost:8070").rstrip("/")

    def available(self) -> bool:
        try:
            import requests
        except ImportError:
            return False
        try:
            resp = requests.get(f"{self.url}/api/isalive", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def parse(self, pdf_path: str | Path, workdir: str | Path) -> ParsedDocument:
        import requests

        pdf_path = Path(pdf_path)
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        if not pdf_path.exists():
            raise ParserError(f"PDF not found: {pdf_path}")

        timeout = getattr(self.config, "parser_timeout", 1800) if self.config else 1800
        logger.info("Sending %s to GROBID at %s", pdf_path.name, self.url)
        try:
            with open(pdf_path, "rb") as fh:
                resp = requests.post(
                    f"{self.url}/api/processFulltextDocument",
                    files={"input": fh},
                    data={"teiCoordinates": "false"},
                    timeout=min(timeout, 600),
                )
        except requests.RequestException as exc:
            raise ParserError(f"GROBID request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ParserError(f"GROBID HTTP {resp.status_code}: {resp.text[:300]}")

        markdown = _tei_to_markdown(resp.text)
        if not markdown.strip():
            raise ParserError("GROBID returned no usable text")
        md_path = workdir / f"{pdf_path.stem}.md"
        md_path.write_text(markdown, encoding="utf-8")
        return ParsedDocument(
            source_path=str(pdf_path),
            markdown=markdown,
            markdown_path=str(md_path),
            backend=self.name,
        )


def _tei_to_markdown(tei_xml: str) -> str:
    """Minimal TEI -> Markdown: title, abstract, sections, paragraphs."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(tei_xml)
    except ET.ParseError as exc:
        raise ParserError(f"GROBID returned unparsable TEI: {exc}") from exc

    def text_of(el) -> str:
        return re.sub(r"\s+", " ", "".join(el.itertext())).strip()

    parts: list[str] = []
    title = root.find(".//tei:titleStmt/tei:title", _TEI_NS)
    if title is not None and text_of(title):
        parts.append(f"# {text_of(title)}")

    abstract = root.find(".//tei:profileDesc/tei:abstract", _TEI_NS)
    if abstract is not None and text_of(abstract):
        parts.append("## Abstract\n\n" + text_of(abstract))

    body = root.find(".//tei:text/tei:body", _TEI_NS)
    if body is not None:
        for div in body.findall("tei:div", _TEI_NS):
            head = div.find("tei:head", _TEI_NS)
            if head is not None and text_of(head):
                parts.append(f"## {text_of(head)}")
            for para in div.findall("tei:p", _TEI_NS):
                if text_of(para):
                    parts.append(text_of(para))

    return "\n\n".join(parts) + "\n"
