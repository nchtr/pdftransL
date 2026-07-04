from pdftransl.config import PipelineConfig
from pdftransl.rag.embeddings import HashingEmbedder, cosine
from pdftransl.rag.glossary import Glossary
from pdftransl.rag.retriever import RAGContextBuilder
from pdftransl.rag.store import TranslationMemory


def test_hashing_embedder_similarity():
    emb = HashingEmbedder(dim=256)
    a, b, c = emb.embed([
        "The transformer model uses attention.",
        "The transformer architecture uses attention mechanisms.",
        "Bananas are yellow fruits full of potassium.",
    ])
    assert cosine(a, b) > cosine(a, c)


def test_tm_exact_and_search(tmp_path):
    tm = TranslationMemory(tmp_path / "tm.db", HashingEmbedder(dim=256))
    tm.add("Neural networks approximate functions.",
           "Нейронные сети аппроксимируют функции.", "en", "ru")
    assert tm.exact_match(
        "Neural networks approximate functions.", "en", "ru"
    ) == "Нейронные сети аппроксимируют функции."
    hits = tm.search("Neural networks can approximate functions.", "en", "ru",
                     top_k=3, min_similarity=0.3)
    assert hits and hits[0]["target"].startswith("Нейронные")


def test_human_correction_overrides_auto(tmp_path):
    tm = TranslationMemory(tmp_path / "tm.db", HashingEmbedder(dim=256))
    tm.add("attention head", "голова внимания", "en", "ru", origin="auto")
    tm.add("attention head", "головка внимания", "en", "ru", origin="human")
    assert tm.exact_match("attention head", "en", "ru") == "головка внимания"
    assert tm.stats()["human_corrections"] == 1


def test_glossary_match(tmp_path):
    gl = Glossary(tmp_path / "gl.db")
    gl.add("embedding", "эмбеддинг", "en", "ru")
    gl.add("loss function", "функция потерь", "en", "ru")
    hits = gl.match("We minimize the loss function.", "en", "ru")
    assert [h["term"] for h in hits] == ["loss function"]


def test_retriever_returns_exact(tmp_path):
    cfg = PipelineConfig(db_path=str(tmp_path / "db.sqlite"))
    tm = TranslationMemory(cfg.db_path, HashingEmbedder(dim=256))
    tm.add("Hello world.", "Привет, мир.", "en", "ru")
    retriever = RAGContextBuilder(cfg, tm=tm, glossary=None)
    ctx = retriever.build("Hello world.")
    assert ctx["exact_match"] == "Привет, мир."
