"""Лёгкая синтаксическая проверка LaTeX-формул (без TeX).

Баланс скобок, парность ``\\begin``/``\\end``, чётность ``$$`` —
типичные артефакты OCR/перевода всплывают в отчёте, а не молча ломают
рендер.
"""

from __future__ import annotations

import re

from pdftransl.models import QAIssue

_FORMULA_RE = re.compile(
    r"\$\$(?P<display>.+?)\$\$"
    r"|(?<!\$)\$(?!\s)(?P<inline>[^$\n]+?)(?<!\s)\$(?!\$)"
    r"|\\begin\{(?P<env>[a-zA-Z*]+)\}(?P<envbody>.*?)\\end\{(?P=env)\}",
    re.DOTALL,
)
_BEGIN_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
_END_RE = re.compile(r"\\end\{([a-zA-Z*]+)\}")


def check_formula(latex: str) -> list[str]:
    """Return a list of problems found in a single formula body."""
    problems = []
    depth = 0
    escaped = False
    for ch in latex:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                problems.append("unbalanced closing brace")
                depth = 0
    if depth > 0:
        problems.append(f"{depth} unclosed brace(s)")
    if escaped:
        problems.append("trailing backslash")

    begins = _BEGIN_RE.findall(latex)
    ends = _END_RE.findall(latex)
    if sorted(begins) != sorted(ends):
        problems.append(f"begin/end mismatch: {begins} vs {ends}")

    stripped = latex.strip()
    if not stripped:
        problems.append("empty formula")
    return problems


def check_document(markdown: str, max_reported: int = 20) -> list[QAIssue]:
    """Scan a markdown document for syntactically broken formulas."""
    issues: list[QAIssue] = []
    for match in _FORMULA_RE.finditer(markdown):
        body = match.group("display") or match.group("inline") or match.group("envbody") or ""
        problems = check_formula(body)
        if problems:
            snippet = body.strip().replace("\n", " ")[:80]
            issues.append(QAIssue(
                "latex_syntax",
                f"formula '{snippet}…': {'; '.join(problems)}",
                "warning",
            ))
        if len(issues) >= max_reported:
            break
    # document-level delimiter parity
    if markdown.count("$$") % 2 != 0:
        issues.append(QAIssue("latex_delimiters", "odd number of $$ in document", "warning"))
    return issues
