"""Markdown -> standalone HTML with KaTeX-rendered formulas.

Self-contained converter built on the pipeline's own block splitter,
so it understands exactly the markdown the pipeline produces
(headings, paragraphs, tables, code, display math, images, HTML).
Images are inlined as data URIs, making the file fully portable —
this HTML is also the input for browser-based PDF rendering.
"""

from __future__ import annotations

import base64
import html as html_mod
import mimetypes
import re
from pathlib import Path
from typing import Optional

from pdftransl.export.katex_assets import katex_head
from pdftransl.models import BlockType
from pdftransl.parsing.splitter import split_markdown

_MAX_INLINE_IMAGE = 4 * 1024 * 1024

_STYLE = """
<style>
  body { font-family: Georgia, 'Times New Roman', serif; max-width: 52rem;
         margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #1a1a1a; }
  h1, h2, h3, h4 { font-family: Helvetica, Arial, sans-serif; line-height: 1.25; }
  img { max-width: 100%; height: auto; display: block; margin: 1rem auto; }
  table { border-collapse: collapse; margin: 1rem 0; width: 100%; }
  th, td { border: 1px solid #999; padding: 0.4rem 0.6rem; text-align: left; }
  th { background: #f0f0f0; }
  pre { background: #f6f6f6; padding: 0.8rem; overflow-x: auto; border-radius: 4px; }
  code { font-family: 'SF Mono', Consolas, monospace; font-size: 0.92em; }
  .math-display { text-align: center; margin: 1rem 0; overflow-x: auto; }
  .bilingual-source { color: #666; font-size: 0.92em; border-left: 3px solid #ddd;
                      padding-left: 0.8rem; margin: 0.3rem 0; }
  @media print { body { max-width: none; margin: 1cm; } }
</style>
"""


def _inline_md(text: str) -> str:
    """Escape HTML, protect math spans, then apply inline markdown."""
    # protect math so escaping/formatting doesn't touch it
    protected: list[str] = []

    def _protect(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    text = re.sub(r"\$\$.+?\$\$|\$(?!\s)[^$\n]+?(?<!\s)\$", _protect, text, flags=re.DOTALL)
    text = html_mod.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img alt="\1" src="\2">', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    def _restore(match: re.Match) -> str:
        return html_mod.escape(protected[int(match.group(1))], quote=False)

    return re.sub(r"\x00(\d+)\x00", _restore, text)


def _image_to_data_uri(src: str, assets_dir: Optional[Path]) -> str:
    if src.startswith(("http://", "https://", "data:")):
        return src
    candidates = []
    if assets_dir is not None:
        candidates.append(assets_dir / src)
        candidates.append(assets_dir / Path(src).name)
    for path in candidates:
        if path.exists() and path.stat().st_size <= _MAX_INLINE_IMAGE:
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{data}"
    return src


def _table_html(text: str) -> str:
    rows = [r for r in text.splitlines() if r.strip().startswith("|")]
    out = ["<table>"]
    header_done = False
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            header_done = True
            continue
        tag = "td" if header_done or out[-1] != "<table>" else "th"
        out.append(
            "<tr>" + "".join(f"<{tag}>{_inline_md(c)}</{tag}>" for c in cells) + "</tr>"
        )
    out.append("</table>")
    return "\n".join(out)


def markdown_to_html_body(
    markdown: str, assets_dir: Optional[str | Path] = None
) -> str:
    assets = Path(assets_dir) if assets_dir else None
    parts: list[str] = []
    for block in split_markdown(markdown):
        text = block.text
        if block.type == BlockType.HEADING:
            match = re.match(r"^(#{1,6})\s*(.*)$", text)
            level = len(match.group(1))
            parts.append(f"<h{level}>{_inline_md(match.group(2))}</h{level}>")
        elif block.type == BlockType.MATH:
            parts.append(f'<div class="math-display">{html_mod.escape(text, quote=False)}</div>')
        elif block.type == BlockType.CODE:
            body = re.sub(r"^```[^\n]*\n?|\n?```$", "", text)
            parts.append(f"<pre><code>{html_mod.escape(body)}</code></pre>")
        elif block.type == BlockType.TABLE:
            parts.append(_table_html(text))
        elif block.type == BlockType.IMAGE:
            match = re.match(r"^\s*!\[([^\]]*)\]\(([^)]*)\)\s*$", text)
            alt, src = match.group(1), match.group(2)
            src = _image_to_data_uri(src, assets)
            parts.append(f'<figure><img alt="{html_mod.escape(alt)}" src="{src}">'
                         + (f"<figcaption>{html_mod.escape(alt)}</figcaption>" if alt else "")
                         + "</figure>")
        elif block.type == BlockType.HTML:
            parts.append(text)
        else:
            body = _inline_md(text).replace("\n", "<br>\n")
            parts.append(f"<p>{body}</p>")
    # rewrite plain <img src> paths that came through inline markdown
    if assets is not None:
        parts = [
            re.sub(
                r'src="(?!data:|https?:)([^"]+)"',
                lambda m: f'src="{_image_to_data_uri(m.group(1), assets)}"',
                p,
            )
            for p in parts
        ]
    return "\n".join(parts)


def markdown_to_html(
    markdown: str,
    title: str = "Translated document",
    assets_dir: Optional[str | Path] = None,
    offline: bool = True,
) -> str:
    body = markdown_to_html_body(markdown, assets_dir)
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{html_mod.escape(title)}</title>\n{katex_head(offline)}{_STYLE}</head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )
