"""Точный прогресс пайплайна.

Раньше у каждой стадии была зашитая доля прогресс-бара независимо от
того, какие стадии реально включены — задача без ревью «замирала» на
60%. build_stage_plan(config) собирает план только из реально
включённых стадий с долями пропорционально их типичной длительности;
StageTracker переводит «доля внутри стадии» в общий 0..1;
estimate_eta_seconds — линейная оценка оставшегося времени (как у
pip/tqdm), самокорректируется по ходу.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover
    from pdftransl.config import PipelineConfig

# (label, relative weight) — weights are unitless, just relative to each
# other; only the ones for enabled stages get renormalized to sum to 1.0.
# Rough sizing from real runs: parsing (MinerU/Nougat) and translation
# dominate; everything else is comparatively quick.
_STAGES: dict[str, tuple[str, float]] = {
    "parse": ("Парсинг PDF", 28.0),
    "split": ("Разбор структуры", 1.0),
    "context": ("Саммари и авто-глоссарий", 5.0),
    "translate": ("Перевод", 42.0),
    "assemble": ("Сборка документа", 1.0),
    "scoring": ("Оценка качества (LLM-судья)", 4.0),
    "review": ("Ревью LLM", 8.0),
    "backtranslation": ("Проверка обратным переводом", 6.0),
    "latex_fix": ("Починка формул", 3.0),
    "figures": ("Описание рисунков", 4.0),
    "export": ("Экспорт форматов", 10.0),
    "render_check": ("Проверка рендера", 2.0),
    "learn": ("Память переводов", 1.0),
}

# Fixed pipeline order; build_stage_plan filters out disabled ones.
_ORDER = [
    "parse", "split", "context", "translate", "assemble", "scoring",
    "review", "backtranslation", "latex_fix", "figures", "export",
    "render_check", "learn",
]


@dataclass
class StagePlanEntry:
    key: str
    label: str
    weight: float   # normalized share of the bar, sums to 1.0 across the plan
    start: float    # cumulative progress where this stage begins

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label,
                "weight": round(self.weight, 4), "start": round(self.start, 4)}


def build_stage_plan(config: "PipelineConfig") -> list[StagePlanEntry]:
    """Ordered list of stages this config will actually run, each given a
    share of the progress bar proportional to its typical cost."""
    enabled = {
        "parse": True, "split": True, "translate": True, "assemble": True,
        "context": config.doc_summary or config.auto_glossary,
        "scoring": config.quality_score,
        "review": config.review,
        "backtranslation": config.backtranslation_check,
        "latex_fix": config.fix_latex,
        "figures": config.describe_figures,
        "export": bool(config.export_formats),
        "render_check": config.render_check and bool(config.export_formats),
        "learn": config.learn,
    }
    keys = [k for k in _ORDER if enabled.get(k)]
    total_weight = sum(_STAGES[k][1] for k in keys) or 1.0
    plan: list[StagePlanEntry] = []
    cumulative = 0.0
    for key in keys:
        label, raw_weight = _STAGES[key]
        weight = raw_weight / total_weight
        plan.append(StagePlanEntry(key=key, label=label, weight=weight, start=cumulative))
        cumulative += weight
    return plan


class StageTracker:
    """Converts per-stage sub-progress into one precise overall number and
    forwards it to the pipeline's ``on_stage(name, overall_progress)``."""

    def __init__(
        self,
        plan: list[StagePlanEntry],
        on_stage: Optional[Callable[[str, float], None]] = None,
    ):
        self.plan = plan
        self._by_key = {s.key: s for s in plan}
        self._on_stage = on_stage
        self.current: float = 0.0

    def enter(self, key: str, fraction: float = 0.0) -> None:
        """Report progress within stage ``key`` (0..1 of that stage's own
        work). Unknown keys (e.g. terminal markers) pass ``fraction``
        through as the overall value directly."""
        entry = self._by_key.get(key)
        if entry is None:
            overall = max(0.0, min(1.0, fraction))
        else:
            overall = entry.start + entry.weight * max(0.0, min(1.0, fraction))
        self.current = overall
        if self._on_stage:
            self._on_stage(key, overall)

    def freeze(self, key: str) -> None:
        """Report a terminal-but-partial state (e.g. paused) without
        advancing progress — stays at wherever the run actually got to,
        instead of jumping to a stage's fixed boundary."""
        if self._on_stage:
            self._on_stage(key, self.current)

    def finish(self) -> None:
        self.current = 1.0
        if self._on_stage:
            self._on_stage("done", 1.0)


def estimate_eta_seconds(
    elapsed_seconds: float,
    progress: float,
    min_progress: float = 0.03,
) -> Optional[float]:
    """Classic linear ETA: assume the remaining work takes as long per
    unit of progress as the work done so far did (``elapsed / progress
    * (1 - progress)``). It's an approximation, not a promise — parsing
    and translation rarely progress at a constant rate — but it's the
    same estimator every progress bar (pip, tqdm...) uses, and it
    self-corrects every tick as more real data comes in.

    Returns ``None`` while progress is too small for the ratio to mean
    anything (right after a job starts) or once it's already done.
    """
    if progress is None or progress < min_progress or progress >= 1.0:
        return None
    return elapsed_seconds * (1.0 - progress) / progress
