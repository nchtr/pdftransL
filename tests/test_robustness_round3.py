"""Audit round 3: oversized-block splitting, checkpoint write
resilience, watchdog timer reset, pandoc-PDF gating.
"""

import time

from pdftransl.masking import Masker
from pdftransl.parsing.splitter import split_markdown
from pdftransl.resources import Watchdog
from pdftransl.translation.checkpoint import Checkpoint
from pdftransl.translation.translator import _split_oversized, build_segments


# ---- oversized paragraph splitting ----------------------------------------

def _monster(n=60):
    return " ".join(
        f"Sentence number {i} describes the network behaviour in detail."
        for i in range(n)
    )


def test_oversized_paragraph_split_at_sentences():
    md = f"# Title\n\n{_monster()}\n\nSmall tail."
    segs = build_segments(split_markdown(md), Masker(), char_budget=1000)
    tr = [s for s in segs if s.kind == "translate"]
    assert len(tr) > 3
    assert all(len(s.source_text) <= 1200 for s in tr)
    joined = " ".join(s.source_text for s in tr)
    for i in (0, 30, 59):                      # nothing lost
        assert f"Sentence number {i}" in joined


def test_single_long_sentence_kept_whole():
    text = ("word " * 300).strip()             # 1499 chars, no boundaries
    assert _split_oversized(text, 1000) == [text]   # < 4x budget


def test_extreme_sentence_falls_back_to_lines():
    text = "\n".join("line of text here" for _ in range(400))  # ~7k chars
    chunks = _split_oversized(text, 1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1100 for c in chunks)
    assert all("line of text here" in c for c in chunks)       # word-safe


def test_tables_never_split():
    rows = "\n".join(f"| r{i} | v{i} |" for i in range(200))
    md = f"| A | B |\n| --- | --- |\n{rows}"
    segs = build_segments(split_markdown(md), Masker(), char_budget=300)
    assert len([s for s in segs if s.kind == "translate"]) == 1


def test_small_docs_grouping_unchanged():
    segs = build_segments(
        split_markdown("Short one.\n\nShort two."), Masker(), char_budget=4000
    )
    assert len([s for s in segs if s.kind == "translate"]) == 1


# ---- checkpoint: disk failure must not fail the segment --------------------

def test_checkpoint_put_survives_write_failure(tmp_path, monkeypatch):
    cp = Checkpoint(tmp_path / "cp.jsonl", "en", "ru")

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom)
    cp.put("Hello", "Привет")                  # must not raise
    assert cp.get("Hello") == "Привет"         # in-memory copy still serves


# ---- watchdog: clock starts at __enter__ ------------------------------------

def test_watchdog_timer_resets_on_enter():
    fired = []
    wd = Watchdog(stall_seconds=0.3, on_stall=lambda idle: fired.append(idle))
    time.sleep(0.35)                           # construction-to-enter delay
    with wd:
        time.sleep(0.15)                       # under the threshold
    assert not fired                            # old clock would have fired


# ---- exporter: pandoc-PDF requires a TeX engine -----------------------------

def test_pandoc_pdf_not_claimed_without_tex(monkeypatch):
    import pdftransl.export.exporter as ex

    monkeypatch.setattr(ex, "_pandoc_path", lambda: "/usr/bin/pandoc")
    monkeypatch.setattr(ex, "_tex_engine", lambda: None)
    assert "pandoc" not in ex.available_engines()["pdf"]
    assert "pandoc" in ex.available_engines()["docx"]   # docx needs no TeX

    monkeypatch.setattr(ex, "_tex_engine", lambda: "/usr/bin/xelatex")
    assert "pandoc" in ex.available_engines()["pdf"]
