"""Tests for the second improvements wave: latex fix, judge scoring,
rate limiter, LaTeX export, glossary-from-corrections, structured
outputs plumbing."""

from pdftransl.config import PipelineConfig
from pdftransl.export.latex import markdown_to_latex
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.llm.ratelimit import RateLimiter
from pdftransl.models import Segment
from pdftransl.quality.latex_fix import fix_document
from pdftransl.quality.scoring import score_segment
from pdftransl.service import TranslationService, looks_like_term


# --- LLM latex repair -----------------------------------------------------

def test_fix_document_repairs_broken_formula():
    md = "Текст с формулой $\\frac{a}{b$ и дальше."
    client = FakeLLMClient(responses=["$\\frac{a}{b}$"])
    fixed_md, fixes = fix_document(md, client, PipelineConfig())
    assert "$\\frac{a}{b}$" in fixed_md
    assert fixes[0]["fixed"] is True


def test_fix_document_keeps_original_when_llm_fails():
    md = "Формула $\\frac{a}{b$ осталась."
    client = FakeLLMClient(responses=["still broken {"])
    fixed_md, fixes = fix_document(md, client, PipelineConfig())
    assert "$\\frac{a}{b$" in fixed_md          # untouched
    assert fixes[0]["fixed"] is False


def test_fix_document_skips_valid_formulas():
    md = "Всё хорошо: $E = mc^2$."
    client = FakeLLMClient(responses=[])
    fixed_md, fixes = fix_document(md, client, PipelineConfig())
    assert fixed_md == md
    assert fixes == []


# --- judge scoring ----------------------------------------------------------

def make_segment():
    seg = Segment(id="s1", kind="translate",
                  source_text="The model works well on benchmarks.")
    seg.masked_text = seg.source_text
    seg.translation = "Модель хорошо работает на бенчмарках."
    return seg


def test_low_score_flags_segment():
    seg = make_segment()
    client = FakeLLMClient(responses=['{"score": 40, "comment": "awkward wording"}'])
    score = score_segment(seg, client, PipelineConfig(quality_score_threshold=70))
    assert score == 40
    assert any(i.code == "low_quality_score" for i in seg.issues)


def test_high_score_passes():
    seg = make_segment()
    client = FakeLLMClient(responses=['{"score": 93, "comment": "good"}'])
    score = score_segment(seg, client, PipelineConfig())
    assert score == 93
    assert not seg.issues


def test_unparsable_score_is_none():
    seg = make_segment()
    client = FakeLLMClient(responses=["great translation!"])
    assert score_segment(seg, client, PipelineConfig()) is None


# --- rate limiter ------------------------------------------------------------

def test_rate_limiter_spacing():
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def fake_clock():
        return clock["t"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    limiter = RateLimiter(rpm=60, clock=fake_clock, sleep=fake_sleep)  # 1 req/s
    limiter.wait()          # first: no wait
    limiter.wait()          # second: ~1s apart
    limiter.wait()
    assert sleeps and abs(sum(sleeps) - 2.0) < 0.01


# --- latex export ---------------------------------------------------------------

def test_markdown_to_latex_structure():
    md = (
        "# Заголовок\n\nАбзац с **жирным** и формулой $E=mc^2$ и 100% скидкой.\n\n"
        "$$\na = b\n$$\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "![Рисунок 1](assets/images/f1.png)\n"
    )
    tex = markdown_to_latex(md, title="Тест")
    assert "\\section{Заголовок}" in tex
    assert "\\textbf{жирным}" in tex
    assert "$E=mc^2$" in tex                       # math untouched
    assert "100\\%" in tex                          # % escaped
    assert "\\[\na = b\n\\]" in tex
    assert "\\begin{tabular}{ll}" in tex
    assert "\\includegraphics" in tex and "f1.png" in tex
    assert tex.strip().endswith("\\end{document}")


# --- glossary from corrections ---------------------------------------------------

def test_term_heuristic():
    assert looks_like_term("attention head")
    assert not looks_like_term("This is a full sentence about the model.")
    assert not looks_like_term("multi\nline")


def test_correction_grows_glossary(tmp_path):
    cfg = PipelineConfig(db_path=str(tmp_path / "db.sqlite"), embedder="hashing")
    service = TranslationService(cfg)
    service.add_correction("attention head", "головка внимания")
    from pdftransl.rag.glossary import Glossary

    terms = Glossary(cfg.db_path).list_all()
    assert any(t["term"] == "attention head" for t in terms)
    # prose corrections must NOT pollute the glossary
    service.add_correction(
        "The model performs well on all benchmarks we tried.",
        "Модель хорошо работает на всех проверенных бенчмарках.",
    )
    assert len(Glossary(cfg.db_path).list_all()) == 1


# --- structured outputs plumbing -----------------------------------------------

def test_reviewer_passes_json_mode():
    from pdftransl.quality.reviewer import Reviewer

    seg = make_segment()
    client = FakeLLMClient(responses=['{"ok": true}'])
    reviewer = Reviewer(client, PipelineConfig(structured_outputs=True))
    reviewer.review_segment(seg)
    assert client.last_response_format == {"type": "json_object"}
