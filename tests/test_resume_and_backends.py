"""Tests for resumable checkpoints, TM auto-export, and the Nougat /
GROBID parser backends."""

import re

import pytest

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import LLMError
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.pipeline import TranslationPipeline
from pdftransl.translation.checkpoint import Checkpoint


# ---- checkpoint unit -----------------------------------------------------

def test_checkpoint_roundtrip(tmp_path):
    cp = Checkpoint(tmp_path / "cp.jsonl", "en", "ru")
    assert cp.get("Hello") is None
    cp.put("Hello", "Привет")
    assert cp.get("Hello") == "Привет"
    # reload from disk
    cp2 = Checkpoint(tmp_path / "cp.jsonl", "en", "ru")
    assert cp2.get("Hello") == "Привет"
    assert cp2.count == 1


def test_checkpoint_lang_pair_isolated(tmp_path):
    cp = Checkpoint(tmp_path / "cp.jsonl", "en", "ru")
    cp.put("Hello", "Привет")
    # a different target language must not read the ru entry
    other = Checkpoint(tmp_path / "cp.jsonl", "en", "de")
    assert other.get("Hello") is None


def test_checkpoint_survives_torn_line(tmp_path):
    path = tmp_path / "cp.jsonl"
    cp = Checkpoint(path, "en", "ru")
    cp.put("A", "А")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"k": "broken", "t":')  # crash mid-write
    reloaded = Checkpoint(path, "en", "ru")
    assert reloaded.get("A") == "А"       # good line still loads


# ---- resume through the pipeline ----------------------------------------

def _fake_ru(masked: str) -> str:
    words = {"Paragraph": "Абзац", "number": "номер", "about": "о",
             "neural": "нейронных", "networks": "сетях", "and": "и",
             "data": "данных", "systems": "систем"}
    return re.sub(r"[A-Za-z]+", lambda m: words.get(m.group(0), m.group(0)), masked)


class FlakyClient(FakeLLMClient):
    def __init__(self, fail_after):
        super().__init__(transform=_fake_ru)
        self.fail_after = fail_after

    def chat(self, messages, temperature=0.2, max_tokens=None, response_format=None):
        if len(self.calls) >= self.fail_after:
            self.calls.append(messages)
            raise LLMError("simulated outage")
        return super().chat(messages, temperature, max_tokens, response_format)


def _cfg(tmp_path):
    return PipelineConfig(
        db_path=str(tmp_path / "db.sqlite"), output_dir=str(tmp_path / "out"),
        review=False, doc_summary=False, auto_glossary=False, learn=False,
        max_workers=1, export_formats=[], embedder="hashing", chunk_char_budget=40,
    )


DOC = "\n\n".join(
    f"Paragraph number {i} about neural networks and data systems." for i in range(8)
)


def test_pipeline_resumes_after_failure(tmp_path):
    # first run fails partway; some segments get checkpointed
    c1 = FlakyClient(fail_after=3)
    r1 = TranslationPipeline(_cfg(tmp_path), client=c1).translate_markdown(
        DOC, tmp_path / "out" / "doc.ru.md"
    )
    assert r1.status == "partial"
    assert r1.report["segments_failed"] > 0
    cp_file = tmp_path / "out" / ".checkpoint.jsonl"
    assert cp_file.exists()
    done_first = len(cp_file.read_text().splitlines())
    assert 0 < done_first < 8

    # second run reuses the checkpoint and only translates the rest
    c2 = FlakyClient(fail_after=999)
    r2 = TranslationPipeline(_cfg(tmp_path), client=c2).translate_markdown(
        DOC, tmp_path / "out" / "doc.ru.md"
    )
    assert r2.status == "completed"
    assert len(c2.calls) < 8               # fewer than a full re-translation
    assert not cp_file.exists()            # cleared on full success


def test_sequential_path_survives_segment_error(tmp_path):
    # a single-worker run must not crash when one segment errors
    client = FlakyClient(fail_after=2)
    cfg = _cfg(tmp_path)
    result = TranslationPipeline(cfg, client=client).translate_markdown(
        DOC, tmp_path / "out" / "d.md"
    )
    assert result.status == "partial"      # completed with flagged segments
    assert result.report["segments_failed"] > 0


# ---- TM auto-export -------------------------------------------------------

def test_tm_autoexport_fires_on_threshold(tmp_path):
    from pdftransl.rag.embeddings import HashingEmbedder
    from pdftransl.rag.store import TranslationMemory

    tm = TranslationMemory(tmp_path / "tm.db", HashingEmbedder(dim=128))
    ds = tmp_path / "dataset.jsonl"
    for i in range(2):
        tm.add(f"source {i}", f"перевод {i}", "en", "ru")
    assert tm.maybe_autoexport(3, ds) is None      # below threshold
    tm.add("source 2", "перевод 2", "en", "ru")
    assert tm.maybe_autoexport(3, ds) == 3         # crossed 3 -> export
    assert ds.exists()
    # does not re-fire until the next multiple
    tm.add("source 3", "перевод 3", "en", "ru")
    assert tm.maybe_autoexport(3, ds) is None


# ---- new backends register ------------------------------------------------

def test_nougat_and_grobid_registered():
    from pdftransl.parsing.base import _all_backends

    backends = _all_backends(PipelineConfig())
    assert "nougat" in backends
    assert "grobid" in backends
    # availability probes must not raise even when the tools are absent
    assert backends["nougat"].available() in (True, False)
    assert backends["grobid"].available() in (True, False)


def test_grobid_tei_to_markdown():
    from pdftransl.parsing.grobid_backend import _tei_to_markdown

    tei = """<?xml version="1.0"?>
    <TEI xmlns="http://www.tei-c.org/ns/1.0">
      <teiHeader><fileDesc><titleStmt><title>A Test Paper</title></titleStmt></fileDesc></teiHeader>
      <text><body>
        <div><head>Introduction</head><p>First paragraph here.</p></div>
      </body></text>
    </TEI>"""
    md = _tei_to_markdown(tei)
    assert "# A Test Paper" in md
    assert "## Introduction" in md
    assert "First paragraph here." in md
