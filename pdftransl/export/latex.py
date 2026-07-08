"""Markdown -> компилируемый LaTeX-проект.

Заголовки -> секции, математика проходит без изменений, таблицы ->
tabular, картинки -> figure. Компилируется xelatex-ом из коробки
(шрифты позволяют). Спецсимволы текста экранируются одним
regex-проходом.
"""

from __future__ import annotations

import re
from pathlib import Path

from pdftransl.models import BlockType
from pdftransl.parsing.splitter import split_markdown

_PREAMBLE = r"""\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage{fontspec}         % xelatex/lualatex
\usepackage{amsmath, amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage[margin=2.5cm]{geometry}
\usepackage{hyperref}
\setmainfont{DejaVu Serif}    % handles Cyrillic; change to taste
\begin{document}
"""

_CLOSING = "\n\\end{document}\n"

_SECTION_BY_LEVEL = {
    1: "section", 2: "subsection", 3: "subsubsection",
    4: "paragraph", 5: "subparagraph", 6: "subparagraph",
}

# characters that must be escaped in LaTeX text mode; replaced in ONE
# regex pass — sequential str.replace re-escaped the braces inserted by
# earlier replacements ("\" became "\textbackslash\{\}")
_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_ESCAPE_RE = re.compile(r"[\\&%#_{}~^]")


def _escape_text(text: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group(0)], text)


def _inline_to_latex(text: str) -> str:
    """Escape text while protecting math spans and applying inline styles."""
    protected: list[str] = []

    def _protect(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    # math and \commands survive as-is
    text = re.sub(
        r"\$\$.+?\$\$|\$(?!\s)[^$\n]+?(?<!\s)\$|\\[a-zA-Z]+(?:\{[^}]*\})*",
        _protect, text, flags=re.DOTALL,
    )
    text = _escape_text(text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\\emph{\1}", text)
    text = re.sub(r"`([^`]+)`", r"\\texttt{\1}", text)
    # links: [text](url) -> \href{url}{text}; images handled at block level
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\\href{\2}{\1}", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], text)


def _table_to_latex(text: str) -> str:
    rows = [r for r in text.splitlines() if r.strip().startswith("|")]
    grid: list[list[str]] = []
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue
        grid.append(cells)
    if not grid:
        return ""
    cols = max(len(r) for r in grid)
    lines = [
        r"\begin{table}[h]", r"\centering",
        r"\begin{tabular}{" + "l" * cols + "}",
        r"\toprule",
    ]
    for i, row_cells in enumerate(grid):
        padded = row_cells + [""] * (cols - len(row_cells))
        lines.append(" & ".join(_inline_to_latex(c) for c in padded) + r" \\")
        if i == 0:
            lines.append(r"\midrule")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines)


def markdown_to_latex(markdown: str, title: str | None = None) -> str:
    parts: list[str] = [_PREAMBLE]
    if title:
        parts.append(f"\\title{{{_escape_text(title)}}}\n\\date{{}}\n\\maketitle\n")
    for block in split_markdown(markdown):
        text = block.text
        if block.type == BlockType.HEADING:
            match = re.match(r"^(#{1,6})\s*(.*)$", text)
            command = _SECTION_BY_LEVEL[len(match.group(1))]
            parts.append(f"\\{command}{{{_inline_to_latex(match.group(2))}}}")
        elif block.type == BlockType.MATH:
            body = text.strip()
            if body.startswith("$$") and body.endswith("$$"):
                body = "\\[\n" + body[2:-2].strip("\n") + "\n\\]"
            parts.append(body)
        elif block.type == BlockType.CODE:
            body = re.sub(r"^```[^\n]*\n?|\n?```$", "", text)
            parts.append("\\begin{verbatim}\n" + body + "\n\\end{verbatim}")
        elif block.type == BlockType.TABLE:
            parts.append(_table_to_latex(text))
        elif block.type == BlockType.IMAGE:
            match = re.match(r"^\s*!\[([^\]]*)\]\(([^)]*)\)\s*$", text)
            alt, src = match.group(1), match.group(2)
            figure = [
                r"\begin{figure}[h]", r"\centering",
                f"\\includegraphics[width=0.85\\linewidth]{{{src}}}",
            ]
            if alt:
                figure.append(f"\\caption{{{_inline_to_latex(alt)}}}")
            figure.append(r"\end{figure}")
            parts.append("\n".join(figure))
        elif block.type == BlockType.HTML:
            continue
        else:
            parts.append(_inline_to_latex(text))
    parts.append(_CLOSING)
    return "\n\n".join(parts)


def export_latex(
    markdown: str, output_path: str | Path, title: str | None = None
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_to_latex(markdown, title), encoding="utf-8")
    return output_path
