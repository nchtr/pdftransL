"""Render the exported HTML in headless Chromium and count KaTeX errors.

The LaTeX syntax check is static; this one asks the actual renderer.
KaTeX is configured with ``throwOnError: false``, so broken formulas
become ``.katex-error`` elements — we open the page, let auto-render
finish and count them. Needs playwright + a Chromium binary (see
PDFTRANSL_CHROMIUM); silently skips when unavailable.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pdftransl.models import QAIssue

logger = logging.getLogger(__name__)


def check_rendered_html(html_path: str | Path, timeout_ms: int = 15000) -> list[QAIssue]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("render check skipped: playwright not installed")
        return []

    executable = os.environ.get("PDFTRANSL_CHROMIUM")
    launch_kwargs = {"executable_path": executable} if executable else {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_kwargs)
            page = browser.new_page()
            page.goto(Path(html_path).resolve().as_uri(), timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            errors = page.evaluate(
                "() => Array.from(document.querySelectorAll('.katex-error'))"
                ".map(e => e.textContent.slice(0, 80))"
            )
            rendered = page.evaluate(
                "() => document.querySelectorAll('.katex').length"
            )
            browser.close()
    except Exception as exc:
        logger.warning("render check failed: %s", exc)
        return []

    issues = [
        QAIssue("katex_error", f"formula fails to render: {text}", "warning")
        for text in errors
    ]
    if rendered == 0 and not errors:
        # KaTeX never ran (offline CDN?) — can't conclude anything
        logger.info("render check inconclusive: KaTeX did not load")
    return issues
