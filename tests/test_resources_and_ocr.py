"""Tests for resource monitoring (memory / stall detection) and the
specialized-OCR-model support (DeepSeek-OCR pairing)."""

import time

import pytest

from pdftransl.config import PipelineConfig, model_supports_vision
from pdftransl.resources import (
    MemoryStats,
    Watchdog,
    memory_stats,
    wait_for_memory,
)


# ---- memory monitoring ---------------------------------------------------

def test_memory_stats_available_or_none():
    stats = memory_stats()
    # on CI we should get a reading; if not, None is acceptable
    if stats is not None:
        assert stats.total_mb > 0
        assert 0 <= stats.available_mb <= stats.total_mb
        assert 0 <= stats.used_pct <= 100


def test_memory_stats_dict():
    s = MemoryStats(total_mb=16000, available_mb=4000)
    d = s.to_dict()
    assert d["total_mb"] == 16000 and d["available_mb"] == 4000
    assert d["used_pct"] == 75.0


def test_wait_for_memory_returns_quickly_when_plenty(monkeypatch):
    import pdftransl.resources as res

    monkeypatch.setattr(res, "memory_stats", lambda: MemoryStats(16000, 8000))
    slept = []
    out = wait_for_memory(1000, timeout=10, sleep=slept.append)
    assert out.available_mb == 8000
    assert not slept                       # never waited


def test_wait_for_memory_waits_then_recovers(monkeypatch):
    import pdftransl.resources as res

    readings = [MemoryStats(16000, 500), MemoryStats(16000, 600),
                MemoryStats(16000, 5000)]

    def fake_stats():
        return readings.pop(0) if readings else MemoryStats(16000, 5000)

    monkeypatch.setattr(res, "memory_stats", fake_stats)
    slept = []
    out = wait_for_memory(2000, timeout=100, sleep=slept.append, poll=1)
    assert out.available_mb >= 2000        # recovered
    assert slept                           # it waited


def test_wait_for_memory_disabled_when_zero(monkeypatch):
    import pdftransl.resources as res

    monkeypatch.setattr(res, "memory_stats", lambda: MemoryStats(16000, 100))
    slept = []
    wait_for_memory(0, timeout=10, sleep=slept.append)  # floor 0 -> no wait
    assert not slept


# ---- watchdog / stall detection ------------------------------------------

def test_watchdog_fires_on_stall():
    fired = []
    with Watchdog(stall_seconds=0.15, on_stall=lambda idle: fired.append(idle)):
        time.sleep(0.4)
    assert fired and fired[0] >= 0.15


def test_watchdog_quiet_when_beating():
    fired = []
    with Watchdog(stall_seconds=0.3, on_stall=lambda idle: fired.append(idle)) as wd:
        for _ in range(6):
            time.sleep(0.08)
            wd.beat()
    assert not fired


# ---- specialized OCR models ----------------------------------------------

def test_specialized_ocr_detected():
    from pdftransl.parsing.vlm_ocr_backend import is_specialized_ocr_model

    assert is_specialized_ocr_model("deepseek-ai/DeepSeek-OCR")
    assert is_specialized_ocr_model("stepfun-ai/GOT-OCR2_0")
    assert not is_specialized_ocr_model("gemma3:12b")
    assert not is_specialized_ocr_model("qwen2.5:14b")


def test_ocr_models_are_vision_capable():
    assert model_supports_vision("deepseek-ai/DeepSeek-OCR")
    assert model_supports_vision("got-ocr2")


def test_specialized_ocr_uses_terse_prompt(tmp_path):
    from PIL import Image

    from pdftransl.llm.fake import FakeLLMClient
    from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

    img = tmp_path / "page.png"
    Image.new("RGB", (40, 40)).save(img)

    class OcrModel(FakeLLMClient):
        model = "deepseek-ai/DeepSeek-OCR"
        supports_vision = True

    class GenVLM(FakeLLMClient):
        model = "gemma3:12b"
        supports_vision = True

    backend = VlmOcrBackend(PipelineConfig())
    ocr_msgs = backend._build_messages(OcrModel(), img)
    gen_msgs = backend._build_messages(GenVLM(), img)

    # specialized OCR: one message, no heavy system prompt, grounding text
    assert len(ocr_msgs) == 1
    assert not any(m.get("role") == "system" for m in ocr_msgs)
    assert "markdown" in ocr_msgs[0]["content"][0]["text"].lower()
    # generic VLM: system prompt + user
    assert any(m.get("role") == "system" for m in gen_msgs)


def test_ocr_prompt_override(tmp_path):
    from PIL import Image

    from pdftransl.llm.fake import FakeLLMClient
    from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

    img = tmp_path / "p.png"
    Image.new("RGB", (30, 30)).save(img)

    class GenVLM(FakeLLMClient):
        model = "gemma3:12b"
        supports_vision = True

    cfg = PipelineConfig(ocr_prompt="Extract text as plain markdown.")
    msgs = VlmOcrBackend(cfg)._build_messages(GenVLM(), img)
    user = msgs[-1]["content"][0]["text"]
    assert user == "Extract text as plain markdown."


def test_deepseek_ocr_preset():
    from pdftransl.config import PROVIDER_PRESETS

    preset = PROVIDER_PRESETS["deepseek_ocr"]
    assert preset.supports_vision and preset.is_local


# ---- OCR artifact cleaning (leaked stop-tokens / hallucinations) ----------

def test_clean_ocr_artifacts_strips_control_tokens():
    from pdftransl.parsing.vlm_ocr_backend import clean_ocr_artifacts

    assert clean_ocr_artifacts("Текст.<|im_end|>") == "Текст."
    assert clean_ocr_artifacts("A<|endoftext|>B") == "AB"
    assert clean_ocr_artifacts("Заголовок</s>") == "Заголовок"
    assert clean_ocr_artifacts("row</angela> end") == "row end"


def test_clean_ocr_artifacts_removes_none_run_but_keeps_word():
    from pdftransl.parsing.vlm_ocr_backend import clean_ocr_artifacts

    assert "None" not in clean_ocr_artifacts("| a | NoneNoneNoneNone |")
    assert "None" in clean_ocr_artifacts("if x is None: pass")  # single is legit


def test_clean_ocr_artifacts_preserves_real_markup():
    from pdftransl.parsing.vlm_ocr_backend import clean_ocr_artifacts

    html = "<table><tr><td>1</td></tr></table>"
    assert clean_ocr_artifacts(html) == html
    assert clean_ocr_artifacts("$E=mc^2$ and **bold**") == "$E=mc^2$ and **bold**"


def test_transcribe_page_cleans_output(tmp_path):
    from PIL import Image

    from pdftransl.llm.fake import FakeLLMClient
    from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

    img = tmp_path / "p.png"
    Image.new("RGB", (30, 30)).save(img)

    class Dirty(FakeLLMClient):
        model = "deepseek-ai/DeepSeek-OCR"
        supports_vision = True

        def chat(self, *a, **k):
            return "Распознанный текст.<|im_end|>"

    out = VlmOcrBackend(PipelineConfig())._transcribe_page(Dirty(), img, 1)
    assert out == "Распознанный текст."


# ---- local vision-model unload (frees VRAM before translation) ------------

def test_unload_local_vision_hits_ollama_endpoint(monkeypatch):
    from pdftransl.config import ProviderConfig
    from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

    class LocalClient:
        model = "deepseek-ai/DeepSeek-OCR"
        config = ProviderConfig(
            name="deepseek_ocr", base_url="http://localhost:11434/v1",
            model="deepseek-ai/DeepSeek-OCR", is_local=True,
        )

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(url=url, json=json)
        return object()

    monkeypatch.setattr("requests.post", fake_post)
    VlmOcrBackend(PipelineConfig(vision_unload_after_ocr=True))._unload_local_vision(
        LocalClient()
    )
    assert captured["url"] == "http://localhost:11434/api/generate"
    assert captured["json"] == {"model": "deepseek-ai/DeepSeek-OCR", "keep_alive": 0}


def test_unload_skipped_for_cloud_and_when_disabled(monkeypatch):
    from pdftransl.config import ProviderConfig
    from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

    calls = []
    monkeypatch.setattr("requests.post", lambda *a, **k: calls.append(1))

    class CloudClient:
        model = "gpt-4o"
        config = ProviderConfig(name="openai", base_url="https://api.openai.com/v1",
                                model="gpt-4o", is_local=False)

    class LocalClient:
        model = "qwen2.5-vl"
        config = ProviderConfig(name="ollama", base_url="http://localhost:11434/v1",
                                model="qwen2.5-vl", is_local=True)

    # cloud -> never unload (nothing local to free)
    VlmOcrBackend(PipelineConfig())._unload_local_vision(CloudClient())
    # local but flag off -> skip
    VlmOcrBackend(PipelineConfig(vision_unload_after_ocr=False))._unload_local_vision(
        LocalClient()
    )
    assert not calls


# ---- OCR on multi-page docs: progress, incremental save, fail-fast --------

def _make_pdf(path, pages=5):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page()
        pg.insert_text((72, 72), f"Page {i + 1} about neural networks.")
    doc.save(str(path))
    doc.close()


def test_ocr_reports_per_page_progress(tmp_path):
    from pdftransl.llm.fake import FakeLLMClient
    from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

    pdf = tmp_path / "d.pdf"
    _make_pdf(pdf, pages=5)

    class VClient(FakeLLMClient):
        model = "qwen2.5-vl"
        supports_vision = True

        def chat(self, *a, **k):
            return "распознанный текст"

    backend = VlmOcrBackend(PipelineConfig(ocr_dpi=100), client=VClient())
    seen = []
    backend.on_page_progress = lambda done, total: seen.append((done, total))
    backend.parse(pdf, tmp_path / "wd")
    assert seen == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]


def test_ocr_writes_incrementally(tmp_path):
    from pdftransl.llm.fake import FakeLLMClient
    from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

    pdf = tmp_path / "d.pdf"
    _make_pdf(pdf, pages=3)
    md_path = tmp_path / "wd" / "d.md"

    class VClient(FakeLLMClient):
        model = "qwen2.5-vl"
        supports_vision = True
        seen_pages = 0

        def chat(self, *a, **k):
            # after the first page's chat, the partial md must already exist
            VClient.seen_pages += 1
            if VClient.seen_pages >= 2:
                assert md_path.exists(), "partial markdown should be on disk mid-run"
            return f"страница {VClient.seen_pages}"

    VlmOcrBackend(PipelineConfig(ocr_dpi=100), client=VClient()).parse(pdf, tmp_path / "wd")
    assert md_path.exists()


def test_ocr_client_budget_clamps_and_restores():
    from pdftransl.config import ProviderConfig
    from pdftransl.parsing.vlm_ocr_backend import _ocr_client_budget

    class Client:
        config = ProviderConfig(name="ollama", base_url="http://localhost:11434/v1",
                                model="qwen2.5-vl", is_local=True,
                                timeout=300.0, max_retries=3)

    c = Client()
    cfg = PipelineConfig(ocr_page_timeout=90, ocr_page_retries=1)
    with _ocr_client_budget(c, cfg):
        assert c.config.timeout == 90.0
        assert c.config.max_retries == 1
    assert c.config.timeout == 300.0        # restored
    assert c.config.max_retries == 3


def test_ocr_client_budget_never_lengthens_timeout():
    from pdftransl.config import ProviderConfig
    from pdftransl.parsing.vlm_ocr_backend import _ocr_client_budget

    class Client:
        config = ProviderConfig(name="x", base_url="u", model="m",
                                timeout=60.0, max_retries=3)

    c = Client()
    with _ocr_client_budget(c, PipelineConfig(ocr_page_timeout=180)):
        assert c.config.timeout == 60.0     # min() — не удлиняем


def test_ocr_client_budget_noop_without_config():
    from pdftransl.llm.fake import FakeLLMClient
    from pdftransl.parsing.vlm_ocr_backend import _ocr_client_budget

    with _ocr_client_budget(FakeLLMClient(), PipelineConfig()):
        pass  # must not raise


# ---- pipeline memory guard integration -----------------------------------

def test_pipeline_records_low_memory(tmp_path, monkeypatch):
    import re

    import pdftransl.resources as res
    from pdftransl.llm.fake import FakeLLMClient
    from pdftransl.pipeline import TranslationPipeline

    # pretend the machine is starved so the guard records a warning
    monkeypatch.setattr(res, "memory_stats", lambda: MemoryStats(16000, 200))

    def fake(masked):
        return re.sub(r"[A-Za-z]+", lambda m: "текст", masked)

    cfg = PipelineConfig(
        db_path=str(tmp_path / "db.sqlite"), output_dir=str(tmp_path / "out"),
        review=False, doc_summary=False, auto_glossary=False, learn=False,
        export_formats=[], embedder="hashing", memory_guard=True,
    )
    pipe = TranslationPipeline(cfg, client=FakeLLMClient(transform=fake))
    pipe._log_memory("test", "job1")
    assert pipe._memory_warning is not None
    assert "low memory" in pipe._memory_warning.lower()
