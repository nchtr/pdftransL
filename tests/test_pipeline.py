"""Offline end-to-end test: markdown in -> translated markdown out,
using the fake LLM client (no network, no PDF parser)."""

import re

from pdftransl.config import PipelineConfig
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.pipeline import TranslationPipeline
from pdftransl.storage.repository import JobRepository

MD = """# Deep Learning

Deep learning models learn hierarchical representations of data samples.

The loss $L(\\theta)$ is minimized with gradient descent optimization.

$$
\\theta_{t+1} = \\theta_t - \\eta \\nabla L(\\theta_t)
$$

Results are shown in the table below and discussed in section results.

| Model | Accuracy |
|---|---|
| CNN | 0.92 |
"""


def fake_translate(masked: str) -> str:
    """Word-level fake EN->RU: keeps placeholders and markdown chars."""
    replacements = {
        "Deep": "Глубокое", "Learning": "обучение", "learning": "обучение",
        "models": "модели", "learn": "изучают", "hierarchical":
        "иерархические", "representations": "представления", "of": "",
        "data": "данных", "samples": "примеров", "The": "", "loss":
        "функция потерь", "is": "", "minimized": "минимизируется", "with":
        "с помощью", "gradient": "градиентного", "descent": "спуска",
        "optimization": "оптимизации", "Results": "Результаты", "are": "",
        "shown": "показаны", "in": "в", "the": "", "table": "таблице",
        "below": "ниже", "and": "и", "discussed": "обсуждаются",
        "section": "разделе", "results": "результаты", "Model": "Модель",
        "Accuracy": "Точность", "CNN": "CNN",
    }
    if masked.startswith("Your previous translation has problems"):
        # repair prompt: extract source between first '---' pair
        parts = masked.split("---")
        masked = parts[1].strip() if len(parts) > 2 else masked

    def repl(match: re.Match) -> str:
        word = match.group(0)
        return replacements.get(word, word)

    return re.sub(r"[A-Za-z]+", repl, masked)


def test_translate_markdown_end_to_end(tmp_path):
    cfg = PipelineConfig(
        db_path=str(tmp_path / "db.sqlite"),
        output_dir=str(tmp_path / "out"),
        review=False,
        embedder="hashing",
        doc_summary=False,
        auto_glossary=False,
    )
    client = FakeLLMClient(transform=fake_translate)
    pipeline = TranslationPipeline(cfg, client=client)
    out_path = tmp_path / "out" / "article.ru.md"
    result = pipeline.translate_markdown(MD, out_path)

    assert result.status == "completed", result.report
    text = out_path.read_text(encoding="utf-8")
    assert "$L(\\theta)$" in text                     # inline math preserved
    assert "\\nabla L(\\theta_t)" in text             # display math preserved
    assert "| CNN | 0.92 |" in text                   # table preserved
    assert "Глубокое" in text                         # translated
    assert result.report["segments_failed"] == 0

    # learning: successful segments stored in the TM
    assert pipeline.tm.stats()["segments"] > 0


def test_tm_reuse_on_second_run(tmp_path):
    cfg = PipelineConfig(
        db_path=str(tmp_path / "db.sqlite"),
        output_dir=str(tmp_path / "out"),
        review=False,
        embedder="hashing",
        doc_summary=False,
        auto_glossary=False,
    )
    client1 = FakeLLMClient(transform=fake_translate)
    p1 = TranslationPipeline(cfg, client=client1)
    p1.translate_markdown(MD, tmp_path / "out" / "a.md")
    calls_first = len(client1.calls)
    assert calls_first > 0

    # second run over the same doc: exact TM matches, zero LLM calls
    client2 = FakeLLMClient(transform=fake_translate)
    p2 = TranslationPipeline(cfg, client=client2)
    result = p2.translate_markdown(MD, tmp_path / "out" / "b.md")
    assert result.status == "completed"
    assert len(client2.calls) == 0


def test_job_repository_roundtrip(tmp_path):
    repo = JobRepository(tmp_path / "jobs.db")
    job_id = repo.create("in.pdf", "out", "en", "ru")
    repo.update(job_id, status="running", stage="translate", progress=0.5)
    job = repo.get(job_id)
    assert job["status"] == "running"
    assert job["stage"] == "translate"
    repo.update(job_id, status="completed", result={"ok": True})
    assert repo.get(job_id)["result"] == {"ok": True}
    assert repo.list()[0]["id"] == job_id
