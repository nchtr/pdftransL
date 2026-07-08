"""LLM-починка синтаксически битых формул.

Каждая проблемная формула отправляется модели; правка принимается
только если проходит ту же самую проверку — хуже не сделаем.
"""

from __future__ import annotations

import logging

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import BaseLLMClient
from pdftransl.quality.latex_check import _FORMULA_RE, check_formula

logger = logging.getLogger(__name__)

_FIX_SYSTEM = """\
You repair broken LaTeX formulas. The user sends one formula that has
a syntax problem. Return ONLY the corrected formula body — same
delimiters as given, no explanations, no markdown fences. Fix syntax
only (braces, environments, escapes); never change the mathematical
meaning."""


def fix_document(
    markdown: str,
    client: BaseLLMClient,
    config: PipelineConfig,
) -> tuple[str, list[dict]]:
    """Try to repair broken formulas in a markdown document.

    Returns the (possibly updated) markdown and a log of attempted
    fixes: ``[{formula, problems, fixed, result}]``.
    """
    fixes: list[dict] = []
    attempts = 0
    result_md = markdown

    for match in _FORMULA_RE.finditer(markdown):
        if attempts >= config.max_latex_fixes:
            break
        original = match.group(0)
        body = (
            match.group("display") or match.group("inline")
            or match.group("envbody") or ""
        )
        problems = check_formula(body)
        if not problems:
            continue
        attempts += 1
        entry = {
            "formula": original[:120],
            "problems": problems,
            "fixed": False,
            "result": None,
        }
        try:
            repaired = client.chat(
                [
                    {"role": "system", "content": _FIX_SYSTEM},
                    {"role": "user", "content": original},
                ],
                temperature=0.0,
            ).strip()
        except Exception as exc:
            logger.warning("LaTeX fix call failed: %s", exc)
            fixes.append(entry)
            continue
        if repaired.startswith("```"):
            repaired = repaired.strip("`").strip()

        # accept only if the repaired version passes the same check
        repaired_body = repaired
        for prefix, suffix in (("$$", "$$"), ("$", "$")):
            if repaired_body.startswith(prefix) and repaired_body.endswith(suffix):
                repaired_body = repaired_body[len(prefix):-len(suffix)]
                break
        if repaired and not check_formula(repaired_body):
            result_md = result_md.replace(original, repaired, 1)
            entry["fixed"] = True
            entry["result"] = repaired[:120]
            logger.info("LaTeX fix applied: %s -> %s", original[:60], repaired[:60])
        fixes.append(entry)

    return result_md, fixes
