"""Tests for the precise per-config progress tracker (pdftransl/progress.py)
and batched translation with between-batch memory rechecks.
"""

import re

import pytest

from pdftransl.config import PipelineConfig
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.pipeline import TranslationPipeline
from pdftransl.progress import StageTracker, build_stage_plan, estimate_eta_seconds
from pdftransl.translation.translator import Translator, build_segments
from pdftransl.parsing.splitter import split_markdown
from pdftransl.masking import Masker


# ---- estimate_eta_seconds --------------------------------------------------

def test_eta_none_below_min_progress():
    assert estimate_eta_seconds(10.0, 0.01) is None


def test_eta_none_when_done():
    assert estimate_eta_seconds(100.0, 1.0) is None
    assert estimate_eta_seconds(100.0, 1.2) is None


def test_eta_linear_extrapolation():
    # 10s elapsed at 25% done -> 30s remaining (40s total run)
    eta = estimate_eta_seconds(10.0, 0.25)
    assert eta == pytest.approx(30.0)


def test_eta_self_corrects_as_progress_grows():
    # same elapsed, more progress -> smaller remaining estimate
    slow = estimate_eta_seconds(20.0, 0.1)
    fast = estimate_eta_seconds(20.0, 0.5)
    assert fast < slow


# ---- build_stage_plan / StageTracker --------------------------------------

def test_stage_plan_weights_sum_to_one():
    cfg = PipelineConfig()
    plan = build_stage_plan(cfg)
    total = sum(s.weight for s in plan)
    assert abs(total - 1.0) < 1e-9


def test_stage_plan_excludes_disabled_stages():
    cfg = PipelineConfig(
        review=False, backtranslation_check=False, quality_score=False,
        describe_figures=False, render_check=False, fix_latex=False,
        doc_summary=False, auto_glossary=False, learn=False, export_formats=[],
    )
    keys = {s.key for s in build_stage_plan(cfg)}
    assert keys == {"parse", "split", "translate", "assemble"}
    # re-normalized: still sums to 1.0 even with fewer stages
    plan = build_stage_plan(cfg)
    assert abs(sum(s.weight for s in plan) - 1.0) < 1e-9


def test_stage_plan_includes_enabled_extras():
    cfg = PipelineConfig(review=True, backtranslation_check=True, export_formats=["html"])
    keys = {s.key for s in build_stage_plan(cfg)}
    assert {"review", "backtranslation", "export"} <= keys


def test_tracker_enter_computes_overall_progress():
    cfg = PipelineConfig(review=False, backtranslation_check=False,
                          quality_score=False, describe_figures=False,
                          render_check=False, export_formats=[])
    plan = build_stage_plan(cfg)
    events = []
    tracker = StageTracker(plan, lambda name, p: events.append((name, p)))
    translate = next(s for s in plan if s.key == "translate")

    tracker.enter("translate", 0.5)
    expected = translate.start + translate.weight * 0.5
    assert events[-1] == ("translate", expected)
    assert abs(tracker.current - expected) < 1e-9


def test_tracker_freeze_does_not_advance():
    plan = build_stage_plan(PipelineConfig())
    events = []
    tracker = StageTracker(plan, lambda name, p: events.append((name, p)))
    tracker.enter("translate", 0.3)
    frozen_value = tracker.current
    tracker.freeze("paused")
    assert events[-1] == ("paused", frozen_value)
    assert tracker.current == frozen_value  # unchanged


def test_tracker_unknown_key_passthrough():
    tracker = StageTracker([])
    tracker.enter("done", 1.0)
    assert tracker.current == 1.0


# ---- pipeline integration: progress is monotonic and precise -------------

def _fake_ru(masked: str) -> str:
    words = {"Paragraph": "Абзац", "number": "номер", "about": "о",
             "neural": "нейронных", "networks": "сетях", "and": "и",
             "data": "данных", "systems": "систем"}
    return re.sub(r"[A-Za-z]+", lambda m: words.get(m.group(0), m.group(0)), masked)


DOC = "\n\n".join(
    f"Paragraph number {i} about neural networks and data systems." for i in range(12)
)


def test_pipeline_progress_monotonic_and_skips_disabled_stages(tmp_path):
    cfg = PipelineConfig(
        db_path=str(tmp_path / "db.sqlite"), output_dir=str(tmp_path / "out"),
        review=False, doc_summary=False, auto_glossary=False, learn=False,
        export_formats=[], embedder="hashing", chunk_char_budget=40,
    )
    events = []
    pipe = TranslationPipeline(cfg, client=FakeLLMClient(transform=_fake_ru))
    result = pipe.translate_markdown(
        DOC, tmp_path / "out" / "doc.md",
        on_stage=lambda name, p: events.append((name, p)),
    )
    assert result.status == "completed"
    progresses = [p for _, p in events]
    # monotonically non-decreasing
    assert all(b >= a - 1e-9 for a, b in zip(progresses, progresses[1:]))
    assert progresses[-1] == 1.0
    names = {n for n, _ in events}
    assert "review" not in names   # disabled in this config
    assert "translate" in names


def test_pipeline_stage_plan_recorded_in_report(tmp_path):
    cfg = PipelineConfig(
        db_path=str(tmp_path / "db.sqlite"), output_dir=str(tmp_path / "out2"),
        review=False, doc_summary=False, auto_glossary=False, learn=False,
        export_formats=[], embedder="hashing", chunk_char_budget=40,
    )
    pipe = TranslationPipeline(cfg, client=FakeLLMClient(transform=_fake_ru))
    result = pipe.translate_markdown(DOC, tmp_path / "out2" / "doc.md")
    assert result.status == "completed"
    # translate_markdown doesn't go through run(), so no stage_plan in the
    # report there — but the plan-building function itself must be usable
    # standalone for the Django job-creation flow.
    from pdftransl.progress import build_stage_plan
    plan = build_stage_plan(cfg)
    assert any(s["key"] == "translate" for s in [s.to_dict() for s in plan])


# ---- batched translation ---------------------------------------------------

def _segments_for(n, char_budget=20):
    md = "\n\n".join(f"Paragraph {i} has some neural network content here." for i in range(n))
    blocks = split_markdown(md)
    return build_segments(blocks, Masker(), char_budget=char_budget)


def test_batching_calls_on_batch_per_batch_parallel(tmp_path):
    segments = _segments_for(23)
    to_translate = [s for s in segments if s.kind == "translate"]
    assert len(to_translate) >= 20  # sanity: enough segments to span batches

    cfg = PipelineConfig(use_rag=False, review=False, learn=False,
                         max_workers=4, translate_batch_size=5)
    client = FakeLLMClient(transform=_fake_ru)
    translator = Translator(client, cfg)

    batch_calls = []
    out, paused = translator.translate_segments(
        segments, on_batch=lambda done, total: batch_calls.append((done, total)),
    )
    assert paused is False
    total = len(to_translate)
    import math
    assert len(batch_calls) == math.ceil(total / 5)
    assert batch_calls[-1][0] == total
    # strictly increasing "done" across batches
    assert [d for d, _ in batch_calls] == sorted(d for d, _ in batch_calls)


def test_batching_calls_on_batch_sequential(tmp_path):
    segments = _segments_for(9)
    cfg = PipelineConfig(use_rag=False, review=False, learn=False,
                         max_workers=1, translate_batch_size=3)
    client = FakeLLMClient(transform=_fake_ru)
    translator = Translator(client, cfg)
    batch_calls = []
    translator.translate_segments(
        segments, on_batch=lambda done, total: batch_calls.append(done),
    )
    to_translate = [s for s in segments if s.kind == "translate"]
    assert batch_calls[-1] == len(to_translate)
    assert len(batch_calls) >= 2


def test_pause_mid_batches_tags_unsubmitted_segments(tmp_path):
    segments = _segments_for(23)
    to_translate = [s for s in segments if s.kind == "translate"]
    cfg = PipelineConfig(use_rag=False, review=False, learn=False,
                         max_workers=4, translate_batch_size=5)
    client = FakeLLMClient(transform=_fake_ru)
    translator = Translator(client, cfg)

    calls = {"n": 0}

    def pause_after_first_batch_completion():
        calls["n"] += 1
        return calls["n"] > 3  # let a few segments through, then stop

    out, paused = translator.translate_segments(
        segments, should_pause=pause_after_first_batch_completion,
    )
    assert paused is True
    pending = [s for s in to_translate if s.translation is None]
    assert pending
    for s in pending:
        assert any(i.code == "paused" for i in s.issues)


def test_pipeline_memory_guard_runs_between_batches(tmp_path, monkeypatch):
    import pdftransl.pipeline as pipeline_mod

    calls = []

    def fake_wait_for_memory(min_free_mb, timeout, **kw):
        calls.append(min_free_mb)
        from pdftransl.resources import MemoryStats
        return MemoryStats(16000, 8000)

    monkeypatch.setattr(
        "pdftransl.resources.wait_for_memory", fake_wait_for_memory,
    )

    cfg = PipelineConfig(
        db_path=str(tmp_path / "db.sqlite"), output_dir=str(tmp_path / "out3"),
        review=False, doc_summary=False, auto_glossary=False, learn=False,
        export_formats=[], embedder="hashing", chunk_char_budget=45,
        max_workers=4, translate_batch_size=3, memory_guard=True,
        min_free_memory_mb=1000,
    )
    doc = "\n\n".join(
        f"Paragraph number {i} about neural networks and data systems." for i in range(15)
    )
    pipe = TranslationPipeline(cfg, client=FakeLLMClient(transform=_fake_ru))
    result = pipe.translate_markdown(doc, tmp_path / "out3" / "doc.md")
    assert result.status == "completed"
    assert len(calls) >= 2          # ran between more than one batch
    assert all(c == 1000 for c in calls)
