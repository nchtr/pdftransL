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

import functools
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


@functools.lru_cache(maxsize=1)
def _playwright_chromium_path() -> Optional[str]:
    """Playwright's downloaded Chromium path, if the binary exists.

    Cached because launching the playwright driver just to read this is
    expensive and the answer is stable within a process.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            exe = p.chromium.executable_path
        return exe if exe and Path(exe).exists() else None
    except Exception:
        return None


def chromium_executable() -> Optional[str]:
    """Path to a usable Chromium binary, or None.

    Prefers ``PDFTRANSL_CHROMIUM``; otherwise checks that playwright's
    own downloaded browser actually exists on disk (its
    ``executable_path`` is reported even when the browser was never
    installed — the source of the "engine claims chromium but export
    fails" bug).
    """
    import os

    override = os.environ.get("PDFTRANSL_CHROMIUM")
    if override and Path(override).exists():
        return override
    return _playwright_chromium_path()


def _chromium_pdf(html_path: Path, output_path: Path) -> bool:
    """Print the KaTeX HTML to PDF with headless Chromium (playwright).

    PDFTRANSL_CHROMIUM can point at an existing Chromium binary when
    playwright's own browser download is unavailable.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    executable = chromium_executable()
    if executable is None:  # no usable browser — don't attempt a noisy launch
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=executable)
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


def _tex_engine() -> Optional[str]:
    """A TeX engine capable of compiling our exported .tex (Unicode-aware)."""
    for name in ("tectonic", "xelatex", "lualatex"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def _xelatex_pdf(
    markdown: str, output_path: Path, assets_dir: Optional[Path], title: str
) -> bool:
    """Compile our own LaTeX export to PDF with xelatex/tectonic.

    Fills the gap when TeX is installed but pandoc isn't: real LaTeX
    typography, native formulas, no browser needed.
    """
    engine = _tex_engine()
    if engine is None:
        return False
    import tempfile

    from pdftransl.export.latex import export_latex

    with tempfile.TemporaryDirectory(prefix="pdftransl_tex_") as tmp:
        tmpdir = Path(tmp)
        tex_path = tmpdir / "document.tex"
        export_latex(markdown, tex_path, title=title)
        if assets_dir is not None and Path(assets_dir).exists():
            # image paths in the markdown are relative to the assets dir
            shutil.copytree(assets_dir, tmpdir / Path(assets_dir).name,
                            dirs_exist_ok=True)
            for sub in Path(assets_dir).iterdir():
                if sub.is_dir():
                    shutil.copytree(sub, tmpdir / sub.name, dirs_exist_ok=True)
        if "tectonic" in Path(engine).name:
            cmd = [engine, "--keep-logs", str(tex_path)]
        else:
            cmd = [engine, "-interaction=nonstopmode", "-halt-on-error",
                   tex_path.name]
        try:
            # two passes for stable layout; tectonic handles reruns itself
            passes = 1 if "tectonic" in Path(engine).name else 2
            for _ in range(passes):
                subprocess.run(cmd, cwd=tmpdir, check=True,
                               capture_output=True, text=True, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            tail = (getattr(exc, "stdout", "") or "")[-600:]
            logger.warning("%s failed: %s", Path(engine).name, tail or exc)
            return False
        produced = tmpdir / "document.pdf"
        if not produced.exists():
            return False
        shutil.copy2(produced, output_path)
    return output_path.exists()


def available_engines() -> dict[str, list[str]]:
    """Which export engines can run in this environment (for the UI)."""
    engines: dict[str, list[str]] = {
        "html": ["builtin"], "latex": ["builtin"], "docx": [], "pdf": [],
    }
    if _pandoc_path():
        engines["docx"].append("pandoc")
        # pandoc-for-PDF shells out to xelatex — without a TeX engine it
        # always fails, so don't claim it (the "engine listed but export
        # fails" bug class)
        if _tex_engine() is not None:
            engines["pdf"].append("pandoc")
    try:
        import docx  # noqa: F401
        engines["docx"].append("python-docx")
    except ImportError:
        pass
    # only report chromium when playwright AND a real browser binary exist
    try:
        import playwright.sync_api  # noqa: F401

        if chromium_executable() is not None:
            engines["pdf"].append("chromium")
    except ImportError:
        pass
    if _tex_engine() is not None:
        engines["pdf"].append("xelatex")
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

    if "latex" in formats:
        from pdftransl.export.latex import export_latex

        tex_path = out_base.with_suffix(".tex")
        export_latex(markdown, tex_path, title=title)
        files["latex"] = str(tex_path)
        engines["latex"] = "builtin"

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
        # pandoc's pdf route needs xelatex; skip the doomed attempt without it
        if _tex_engine() is not None and _pandoc_export(md_path, pdf_path, assets):
            files["pdf"], engines["pdf"] = str(pdf_path), "pandoc"
        elif _chromium_pdf(html_path, pdf_path):
            files["pdf"], engines["pdf"] = str(pdf_path), "chromium"
        elif _xelatex_pdf(markdown, pdf_path, assets, title):
            files["pdf"], engines["pdf"] = str(pdf_path), "xelatex"
        elif _weasyprint_pdf(html_text, pdf_path, base_url=str(out_base.parent)):
            files["pdf"], engines["pdf"] = str(pdf_path), "weasyprint"
        else:
            files["pdf"] = None
            engines["pdf"] = (
                "unavailable: install pandoc, playwright (chromium), "
                "a TeX engine (xelatex/tectonic) or weasyprint"
            )

    if md_path.exists():
        md_path.unlink()  # temp copy for pandoc
    if "html" not in formats and html_path.exists():
        html_path.unlink()  # was only the chromium-PDF input, not requested
    return {"files": files, "engines": engines}
