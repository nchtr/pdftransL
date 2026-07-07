"""Locate and inline a vendored KaTeX distribution.

The HTML export renders formulas with KaTeX. Loading it from a CDN
breaks the two things users care about most: offline viewing and the
Chromium PDF path (which runs from a ``file://`` URL with no network).
When a KaTeX build is available locally we inline its CSS, fonts
(as data URIs) and JS, producing a fully self-contained page. If it
is not vendored we fall back to the CDN ``<link>``/``<script>`` tags.

Where we look, in order:
1. ``PDFTRANSL_KATEX_DIR`` (a KaTeX ``dist`` directory)
2. ``frontend/node_modules/katex/dist`` next to the repo
"""

from __future__ import annotations

import base64
import functools
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CDN_HEAD = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body, {delimiters: [
    {left: '$$', right: '$$', display: true},
    {left: '\\\\[', right: '\\\\]', display: true},
    {left: '$', right: '$', display: false},
    {left: '\\\\(', right: '\\\\)', display: false}
  ], throwOnError: false});"></script>
"""

_AUTO_RENDER_CALL = """
<script>
  (function () {
    function run() {
      if (typeof renderMathInElement !== 'function') return;
      renderMathInElement(document.body, {delimiters: [
        {left: '$$', right: '$$', display: true},
        {left: '\\\\[', right: '\\\\]', display: true},
        {left: '$', right: '$', display: false},
        {left: '\\\\(', right: '\\\\)', display: false}
      ], throwOnError: false});
    }
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', run);
    } else { run(); }
  })();
</script>
"""

_FONT_MIME = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}


def find_katex_dist() -> Optional[Path]:
    """Return the KaTeX ``dist`` directory if one is available."""
    env = os.environ.get("PDFTRANSL_KATEX_DIR")
    if env and (Path(env) / "katex.min.css").exists():
        return Path(env)
    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / "frontend" / "node_modules" / "katex" / "dist"
    if (candidate / "katex.min.css").exists():
        return candidate
    return None


def _inline_fonts(css: str, dist: Path) -> str:
    """Replace ``url(fonts/X.woff2)`` with data URIs; drop woff/ttf
    fallbacks so the page never reaches out over the network."""
    fonts_dir = dist / "fonts"

    def _woff2(match: re.Match) -> str:
        name = match.group(1)
        path = fonts_dir / name
        if not path.exists():
            return match.group(0)
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"url(data:font/woff2;base64,{data}) format(\"woff2\")"

    css = re.sub(r"url\(fonts/([^)]+\.woff2)\)\s*format\([^)]*\)", _woff2, css)
    # remove now-redundant woff / ttf sources (relative paths would 404)
    css = re.sub(r",\s*url\(fonts/[^)]+\.(?:woff|ttf)\)\s*format\([^)]*\)", "", css)
    return css


@functools.lru_cache(maxsize=2)
def _build_inline_head(dist_str: str) -> str:
    dist = Path(dist_str)
    css = (dist / "katex.min.css").read_text(encoding="utf-8")
    css = _inline_fonts(css, dist)
    katex_js = (dist / "katex.min.js").read_text(encoding="utf-8")
    auto_js = (dist / "contrib" / "auto-render.min.js").read_text(encoding="utf-8")
    return (
        f"<style>{css}</style>\n"
        f"<script>{katex_js}</script>\n"
        f"<script>{auto_js}</script>\n"
        f"{_AUTO_RENDER_CALL}"
    )


def katex_head(offline: bool = True) -> str:
    """HTML ``<head>`` snippet that renders math.

    Uses a vendored, fully-inlined KaTeX build when available (works
    offline and in headless-browser PDF rendering); otherwise falls
    back to the CDN version.
    """
    if offline:
        dist = find_katex_dist()
        if dist is not None:
            try:
                return _build_inline_head(str(dist))
            except OSError as exc:  # pragma: no cover - unreadable vendor dir
                logger.warning("KaTeX inline failed (%s); using CDN", exc)
    return _CDN_HEAD


def is_offline_capable() -> bool:
    """Whether formulas will render without network access."""
    return find_katex_dist() is not None
