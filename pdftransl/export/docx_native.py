"""DOCX export via python-docx (fallback when pandoc is absent).

Replicates the document structure: heading levels, paragraphs with
bold/italic/code runs, tables, embedded images. Formulas are kept as
literal LaTeX in a monospace style — converting LaTeX to native Word
OMML equations is only supported through the pandoc engine.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from pdftransl.models import BlockType
from pdftransl.parsing.splitter import split_markdown

_INLINE_RE = re.compile(
    r"(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`|\$[^$\n]+\$)"
)


def _add_runs(paragraph, text: str) -> None:
    """Split inline markdown into styled runs."""
    for part in _INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            run = paragraph.add_run(part[1:-1])
            run.italic = True
        elif part.startswith("`") and part.endswith("`"):
            run = paragraph.add_run(part[1:-1])
            run.font.name = "Consolas"
        elif part.startswith("$") and part.endswith("$"):
            run = paragraph.add_run(part)
            run.font.name = "Cambria Math"
        else:
            # strip residual link/image syntax to plain text
            clean = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", part)
            paragraph.add_run(clean)


def _find_image(src: str, assets_dir: Optional[Path]) -> Optional[Path]:
    if assets_dir is None:
        return None
    for candidate in (assets_dir / src, assets_dir / Path(src).name):
        if candidate.exists():
            return candidate
    return None


def export_docx(
    markdown: str,
    output_path: str | Path,
    assets_dir: Optional[str | Path] = None,
    title: str = "",
) -> Path:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    assets = Path(assets_dir) if assets_dir else None

    document = docx.Document()
    if title:
        document.core_properties.title = title

    for block in split_markdown(markdown):
        text = block.text
        if block.type == BlockType.HEADING:
            match = re.match(r"^(#{1,6})\s*(.*)$", text)
            document.add_heading(match.group(2), level=min(len(match.group(1)), 9))
        elif block.type == BlockType.MATH:
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(text)
            run.font.name = "Cambria Math"
            run.font.size = Pt(10)
        elif block.type == BlockType.CODE:
            body = re.sub(r"^```[^\n]*\n?|\n?```$", "", text)
            paragraph = document.add_paragraph()
            run = paragraph.add_run(body)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif block.type == BlockType.TABLE:
            rows = [r for r in text.splitlines() if r.strip().startswith("|")]
            grid = []
            for row in rows:
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
                    continue
                grid.append(cells)
            if grid:
                cols = max(len(r) for r in grid)
                table = document.add_table(rows=len(grid), cols=cols)
                table.style = "Light Grid Accent 1"
                for i, row_cells in enumerate(grid):
                    for j, cell in enumerate(row_cells):
                        _add_runs(table.cell(i, j).paragraphs[0], cell)
        elif block.type == BlockType.IMAGE:
            match = re.match(r"^\s*!\[([^\]]*)\]\(([^)]*)\)\s*$", text)
            path = _find_image(match.group(2), assets)
            if path is not None:
                try:
                    document.add_picture(str(path), width=Inches(5.5))
                    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                except Exception:
                    document.add_paragraph(f"[figure: {match.group(2)}]")
            else:
                document.add_paragraph(f"[figure: {match.group(2)}]")
            if match.group(1):
                caption = document.add_paragraph(match.group(1))
                caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                caption.runs[0].italic = True
        elif block.type == BlockType.HTML:
            continue  # raw html has no docx representation
        else:
            paragraph = document.add_paragraph()
            _add_runs(paragraph, text.replace("\n", " "))

    document.save(str(output_path))
    return output_path
