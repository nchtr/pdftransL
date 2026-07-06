"""Render LaTeX formulas to PNG images (for DOCX without pandoc).

python-docx cannot typeset LaTeX, so without pandoc formulas used to
land in the document as raw ``$...$`` text — unreadable. When
matplotlib is available we rasterize each formula with its mathtext
engine and embed the image, so the DOCX shows an actual formula.
mathtext covers a solid subset of math; anything it can't parse
(e.g. ``\\begin{aligned}``) raises and the caller falls back to text.
pandoc remains the path to native, editable Word equations.
"""

from __future__ import annotations

import functools
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_MATPLOTLIB_OK: Optional[bool] = None


def matplotlib_available() -> bool:
    global _MATPLOTLIB_OK
    if _MATPLOTLIB_OK is None:
        try:
            import matplotlib  # noqa: F401
            _MATPLOTLIB_OK = True
        except ImportError:
            _MATPLOTLIB_OK = False
    return _MATPLOTLIB_OK


def strip_math_delimiters(text: str) -> str:
    """Return the inner LaTeX of a formula string, delimiters removed."""
    text = text.strip()
    for left, right in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if text.startswith(left) and text.endswith(right) and len(text) > len(left) + len(right) - 1:
            return text[len(left):-len(right)].strip()
    return text


@functools.lru_cache(maxsize=256)
def render_latex_png(
    latex_body: str,
    fontsize: int = 14,
    dpi: int = 200,
) -> Optional[bytes]:
    """Rasterize a single formula (LaTeX body, no delimiters) to PNG.

    Returns ``None`` when matplotlib is missing or the formula is
    outside mathtext's supported subset — the caller should then keep
    the formula as text.
    """
    if not matplotlib_available() or not latex_body.strip():
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # pragma: no cover - broken matplotlib install
        return None

    body = latex_body.strip()
    # mathtext takes a single-$ math string; normalize a few constructs
    body = body.replace("\\ ", " ").replace("\\,", " ").replace("\\!", "")
    try:
        fig = plt.figure(figsize=(0.01, 0.01))
        fig.text(0, 0, f"${body}$", fontsize=fontsize)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                    pad_inches=0.05, transparent=False)
        plt.close(fig)
        data = buf.getvalue()
        return data if data else None
    except Exception as exc:
        logger.debug("mathtext render failed for %r: %s", latex_body[:40], exc)
        try:
            plt.close("all")
        except Exception:
            pass
        return None
