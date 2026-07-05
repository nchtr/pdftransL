"""Format export orchestration with graceful engine fallback.

Engines per format, tried in order of output fidelity:

- docx: pandoc (native Word equations via OMML) -> python-docx
  (structure + images, formulas as LaTeX text)
- pdf:  pandoc+xelatex (true typography) -> headless Chromium print
  of our KaTeX HTML (rendered formulas, needs playwright) ->
  weasyprint (text formulas)
- html: built-in KaTeX exporter (always available)

The result dict maps format -> output path (or None with a reason in
the 'engines' entry), so callers and the QA report can tell the user
exactly what was produced and how.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from pdftransl.export.html import markdown_to_html

logger = logging.getLogger(__name__)


def _pandoc_path() -> Optional[str]:
    return shutil.which("pandoc")


def _pandoc_export(
    md_path: Path, output_path: Path, assets_dir: Optional[Path]
) -> bool:
    pandoc = _pandoc_path()
    if not pandoc:
        return False
    cmd = [
        pandoc, str(md_path), "-o", str(output_path),
        "--standalone", "--from", "markdown+tex_math_dollars",
    ]
    if assets_dir is not None:
        cmd += ["--resource-path", str(assets_dir)]
    if output_path.suffix == ".pdf":
        cmd += ["--pdf-engine=xelatex", "-V", "mainfont=DejaVu Serif"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        return output_path.exists()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        logger.warning("pandoc export to %s failed: %s", output_path.suffix, stderr[-500:])
        return False


def _chromium_pdf(html_path: Path, output_path: Path) -> bool:
    """Print the KaTeX HTML to PDF with headless Chromium (playwright).

    PDFTRANSL_CHROMIUM can point at an existing Chromium binary when
    playwright's own browser download is unavailable.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    import os

    executable = os.environ.get("PDFTRANSL_CHROMIUM")
    launch_kwargs = {"executable_path": executable} if executable else {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_kwargs)
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri())
            page.wait_for_load_state("networkidle")
            page.pdf(path=str(output_path), format="A4",
                     margin={"top": "2cm", "bottom": "2cm",
                             "left": "2cm", "right": "2cm"})
            browser.close()
        return output_path.exists()
    except Exception as exc:
        logger.warning("chromium pdf failed: %s", exc)
        return False


def _weasyprint_pdf(html_text: str, output_path: Path, base_url: str) -> bool:
    try:
        from weasyprint import HTML
    except Exception:
        return False
    try:
        HTML(string=html_text, base_url=base_url).write_pdf(str(output_path))
        return output_path.exists()
    except Exception as exc:
        logger.warning("weasyprint pdf failed: %s", exc)
        return False


def available_engines() -> dict[str, list[str]]:
    """Which export engines can run in this environment (for the UI)."""
    engines: dict[str, list[str]] = {"html": ["builtin"], "docx": [], "pdf": []}
    if _pandoc_path():
        engines["docx"].append("pandoc")
        engines["pdf"].append("pandoc")
    try:
        import docx  # noqa: F401
        engines["docx"].append("python-docx")
    except ImportError:
        pass
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        engines["pdf"].append("chromium")
    except ImportError:
        pass
    try:
        import weasyprint  # noqa: F401
        engines["pdf"].append("weasyprint")
    except Exception:
        pass
    return engines


def export_document(
    markdown: str,
    out_base: str | Path,
    formats: list[str],
    assets_dir: Optional[str | Path] = None,
    title: str = "Translated document",
) -> dict:
    """Export markdown to the requested formats.

    ``out_base`` is the extension-less output path
    (``.../article.ru`` -> ``article.ru.html`` / ``.docx`` / ``.pdf``).
    Returns ``{"files": {fmt: path|None}, "engines": {fmt: engine|reason}}``.
    """
    out_base = Path(out_base)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    assets = Path(assets_dir) if assets_dir else None
    files: dict[str, Optional[str]] = {}
    engines: dict[str, str] = {}

    # HTML first: it is also the input for the chromium PDF engine.
    html_text = markdown_to_html(markdown, title=title, assets_dir=assets)
    html_path = out_base.with_suffix(".html")
    if "html" in formats or "pdf" in formats:
        html_path.write_text(html_text, encoding="utf-8")
    if "html" in formats:
        files["html"] = str(html_path)
        engines["html"] = "builtin"

    md_path = out_base.with_suffix(".export.md")

    if "docx" in formats:
        docx_path = out_base.with_suffix(".docx")
        md_path.write_text(markdown, encoding="utf-8")
        if _pandoc_export(md_path, docx_path, assets):
            files["docx"], engines["docx"] = str(docx_path), "pandoc"
        else:
            try:
                from pdftransl.export.docx_native import export_docx

                export_docx(markdown, docx_path, assets_dir=assets, title=title)
                files["docx"], engines["docx"] = str(docx_path), "python-docx"
            except ImportError:
                files["docx"] = None
                engines["docx"] = "unavailable: install pandoc or python-docx"

    if "pdf" in formats:
        pdf_path = out_base.with_suffix(".pdf")
        if not md_path.exists():
            md_path.write_text(markdown, encoding="utf-8")
        if _pandoc_export(md_path, pdf_path, assets):
            files["pdf"], engines["pdf"] = str(pdf_path), "pandoc"
        elif _chromium_pdf(html_path, pdf_path):
            files["pdf"], engines["pdf"] = str(pdf_path), "chromium"
        elif _weasyprint_pdf(html_text, pdf_path, base_url=str(out_base.parent)):
            files["pdf"], engines["pdf"] = str(pdf_path), "weasyprint"
        else:
            files["pdf"] = None
            engines["pdf"] = (
                "unavailable: install pandoc (+xelatex), playwright or weasyprint"
            )

    if md_path.exists():
        md_path.unlink()  # temp copy for pandoc
    return {"files": files, "engines": engines}
