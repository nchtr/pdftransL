"""Тесты v0.19 — исправления по итогам ручной проверки двух реальных
документов (сборник конференции RU->EN, статья RU->JA):

1. порядок фолбэка парсеров: здоровый текстовый слой -> pymupdf раньше VLM-OCR;
2. пустые страницы OCR: ретрай + спасение текстовым слоем + учёт потерь;
3. честный статус: неполное покрытие -> partial + coverage_warning;
4. валидатор длины с поправкой на плотность CJK;
5. ревьюер не падает на notes:null / не-dict вердикте и не обрывает стадию;
6. спасение сегмента переводом без маскировки;
7. HTML-таблицы -> Markdown;
8. сломанная установка MinerU запоминается.
"""

import re

import pytest

from pdftransl.config import PipelineConfig
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.masking import Masker
from pdftransl.models import QAIssue, Segment
from pdftransl.parsing.html_tables import convert_html_tables
from pdftransl.parsing.splitter import split_markdown
from pdftransl.quality.reviewer import Reviewer, _parse_review
from pdftransl.quality.validators import _length_scale, validate_segment
from pdftransl.translation.translator import Translator, build_segments


# ---- 4. CJK-осознанный валидатор длины -------------------------------------

def _seg(source: str, translation: str) -> Segment:
    seg = Segment(id="s1", kind="translate", source_text=source,
                  masked_text=source)
    seg.translation = translation
    return seg


RU_PARA = (
    "Внутренние войска, являющиеся специфической частью военной организации "
    "России, входят в систему МВД. Их история советского периода берет "
    "начало в 1917 году, когда были созданы боевые отряды, являвшиеся "
    "составной частью армии. На них возлагалась ответственность за "
    "сохранение порядка и твёрдости фронта." * 2
)
JA_SHORT = (
    "ロシアの内部部隊は、軍事組織の特定部分として内務省の体系に属する。"
    "その歴史はソ連時代の1917年に遡り、当時創設された戦闘部隊は軍の"
    "構成部分であった。これらの部隊は秩序の維持と前線の堅固さに責任を負った。"
)


def test_length_scale_directions():
    assert _length_scale("ru", "ja") == pytest.approx(2.5)
    assert _length_scale("ja", "ru") == pytest.approx(1 / 2.5)
    assert _length_scale("ru", "en") == 1.0
    assert _length_scale("zh", "ja") == 1.0  # оба CJK — без поправки


def test_short_japanese_translation_not_flagged():
    """Нормальный JA-перевод в ~3 раза короче RU-оригинала в символах —
    раньше это давало ложное too_short и запускало цикл починки."""
    cfg = PipelineConfig(source_lang="ru", target_lang="ja")
    seg = _seg(RU_PARA, JA_SHORT)
    codes = [i.code for i in validate_segment(seg, cfg)]
    assert "too_short" not in codes


def test_truly_truncated_japanese_still_flagged():
    cfg = PipelineConfig(source_lang="ru", target_lang="ja")
    seg = _seg(RU_PARA, "ロシアの")  # обрубок
    codes = [i.code for i in validate_segment(seg, cfg)]
    assert "too_short" in codes


def test_alphabetic_pairs_unchanged():
    cfg = PipelineConfig(source_lang="ru", target_lang="en")
    seg = _seg(RU_PARA, "Short.")
    codes = [i.code for i in validate_segment(seg, cfg)]
    assert "too_short" in codes


# ---- 5. Ревьюер: null-notes, не-dict вердикт, изоляция сегмента ------------

def test_parse_review_rejects_non_dict():
    assert _parse_review("null") is None
    assert _parse_review('"just a string"') is None
    assert _parse_review("[1, 2]") is None
    assert _parse_review('{"ok": true}') == {"ok": True}


def test_reviewer_survives_null_notes():
    """{"notes": null} раньше ронял ревью всего документа TypeError'ом."""
    md = "The value $x_1$ appears in the model output many times."
    blocks = split_markdown(md)
    segments = build_segments(blocks, Masker(), char_budget=4000)
    seg = segments[0]
    token = next(iter(seg.placeholders))
    seg.translation = f"Значение {token} много раз появляется в выводе."
    seg.issues = [QAIssue("too_short", "flagged", "warning")]

    revised = f"Значение {token} появляется в выводе модели много раз."
    client = FakeLLMClient(responses=[
        '{"ok": false, "revised": "%s", "notes": null}' % revised
    ])
    cfg = PipelineConfig(source_lang="ru", target_lang="ru", review=True)
    Reviewer(client, cfg).review_segments([seg], only_flagged=True)
    assert any(i.code == "reviewed_revised" for i in seg.issues)


def test_review_segments_isolates_crashing_segment(monkeypatch):
    """Сбой ревью одного сегмента не должен лишать ревью остальные."""
    cfg = PipelineConfig(source_lang="ru", target_lang="en", review=True)
    reviewer = Reviewer(FakeLLMClient(responses=['{"ok": true}']), cfg)

    seg1 = _seg("source one text here", "translated one")
    seg1.issues = [QAIssue("too_short", "x", "warning")]
    seg2 = _seg("source two text here", "translated two")
    seg2.issues = [QAIssue("too_short", "x", "warning")]

    original = reviewer.review_segment
    calls = {"n": 0}

    def flaky(segment):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TypeError("'NoneType' object is not subscriptable")
        return original(segment)

    monkeypatch.setattr(reviewer, "review_segment", flaky)
    reviewer.review_segments([seg1, seg2], only_flagged=True)
    assert any(i.code == "review_error" for i in seg1.issues)
    assert any(i.code == "reviewed_ok" for i in seg2.issues)  # дошли до второго


# ---- 6. Спасение переводом без маскировки ----------------------------------

def test_unmasked_rescue_saves_placeholder_loser():
    """Модель упорно теряет ⟦PH⟧-токены, но нормально переводит без них:
    сегмент должен спастись, а формула — пережить перевод дословно."""
    md = "The value $x_1$ appears in the output of the model system."
    blocks = split_markdown(md)
    segments = build_segments(blocks, Masker(), char_budget=4000)
    seg = segments[0]

    bad = "Значение появляется в выводе модели системы постоянно."  # токен потерян
    rescue = "Значение $x_1$ появляется в выводе модельной системы постоянно."
    # 1-я попытка + 1 ремонт (дальше цикл выходит по повтору) + спасение
    client = FakeLLMClient(responses=[bad, bad, rescue])
    cfg = PipelineConfig(use_rag=False, review=False, learn=False,
                         source_lang="en", target_lang="ru",
                         max_repair_attempts=2)
    Translator(client, cfg).translate_segment(seg)
    assert seg.ok, [i.code for i in seg.issues]
    assert "$x_1$" in seg.translation
    assert any(i.code == "unmasked_rescue" for i in seg.issues)
    assert seg.final_text() == seg.translation  # не откат на оригинал


def test_unmasked_rescue_rejects_dropped_formula():
    """Спасение без маскировки принимается ТОЛЬКО если защищённое
    содержимое дословно на месте — иначе это потеря формулы."""
    md = "The value $x_1$ appears in the output of the model system."
    blocks = split_markdown(md)
    segments = build_segments(blocks, Masker(), char_budget=4000)
    seg = segments[0]

    bad = "Значение появляется в выводе модели системы постоянно."
    client = FakeLLMClient(responses=[bad, bad, bad])  # и спасение без формулы
    cfg = PipelineConfig(use_rag=False, review=False, learn=False,
                         source_lang="en", target_lang="ru",
                         max_repair_attempts=2)
    Translator(client, cfg).translate_segment(seg)
    assert not seg.ok
    assert seg.final_text() == seg.source_text  # честный откат


def test_unmasked_rescue_disabled_by_flag():
    md = "The value $x_1$ appears in the output of the model system."
    blocks = split_markdown(md)
    segments = build_segments(blocks, Masker(), char_budget=4000)
    seg = segments[0]
    bad = "Значение появляется в выводе модели системы постоянно."
    client = FakeLLMClient(responses=[bad, bad, bad])
    cfg = PipelineConfig(use_rag=False, review=False, learn=False,
                         source_lang="en", target_lang="ru",
                         max_repair_attempts=2, unmasked_rescue=False)
    Translator(client, cfg).translate_segment(seg)
    assert len(client.calls) == 2  # без третьего, спасательного вызова


# ---- 7. HTML-таблицы -> Markdown -------------------------------------------

def test_convert_full_html_table():
    html = ("<table><tr><th>Criteria</th><th>NS-2</th></tr>"
            "<tr><td>Source code</td><td>Open</td></tr></table>")
    md = convert_html_tables(html)
    assert "<table>" not in md
    assert "| Criteria | NS-2 |" in md
    assert "|---|---|" in md
    assert "| Source code | Open |" in md


def test_convert_bare_rows_without_table_tag():
    text = ("Preceding text.\n"
            "<tr><td>симулятор старше</td><td>больше моделей</td></tr>"
            "<tr><td>NAM</td><td>PyViz</td></tr>\n"
            "Following text.")
    md = convert_html_tables(text)
    assert "</td><td>" not in md
    assert "| симулятор старше | больше моделей |" in md
    assert "Preceding text." in md and "Following text." in md


def test_convert_handles_ragged_rows_and_pipes():
    html = "<table><tr><td>a|b</td><td>c</td></tr><tr><td>only-one</td></tr></table>"
    md = convert_html_tables(html)
    assert r"a\|b" in md               # вертикальная черта экранирована
    assert "| only-one |  |" in md     # короткий ряд добит пустыми ячейками


def test_convert_leaves_tableless_text_alone():
    text = "Plain paragraph with <b>bold</b> and a < sign."
    assert convert_html_tables(text) == text


def test_converted_table_becomes_translatable_blocks():
    """Смысл конвертации: markdown-таблица попадает в перевод, а не
    в непереводимый HTML-блок."""
    from pdftransl.models import BlockType

    html = ("<table><tr><td>Заголовок</td><td>Значение</td></tr>"
            "<tr><td>строка</td><td>данные</td></tr></table>")
    blocks = split_markdown(convert_html_tables(html))
    assert any(b.type == BlockType.TABLE and b.translatable for b in blocks)


# ---- 2/3. OCR: пустые страницы, спасение, покрытие -------------------------

@pytest.fixture()
def three_page_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "doc.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        text = (f"Page {i + 1}. " + "Meaningful body text for extraction. " * 8)
        page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


class _VisionFake(FakeLLMClient):
    supports_vision = True


def test_ocr_empty_page_rescued_from_text_layer(tmp_path, three_page_pdf):
    """Пустой ответ OCR-модели: ретрай, затем текст со страницы PDF —
    страница не исчезает из документа молча."""
    from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

    # страница 2: оба вызова (основной + ретрай) пустые -> спасение слоем
    responses = ["page one text", "", "", "page three text"]
    client = _VisionFake(responses=responses)
    cfg = PipelineConfig(ocr_dpi=72)
    backend = VlmOcrBackend(cfg, client=client)
    parsed = backend.parse(three_page_pdf, tmp_path / "work")

    assert parsed.meta.get("pages_rescued") == [2]
    assert "pages_empty" not in parsed.meta
    assert "Page 2." in parsed.markdown          # текст спасён из слоя
    assert "page one text" in parsed.markdown


def test_ocr_counts_truly_lost_pages(tmp_path):
    """Скан без текстового слоя + пустой OCR = страница честно помечена
    потерянной (meta.pages_empty), а не тихо пропущена."""
    fitz = pytest.importorskip("fitz")
    from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

    path = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page()  # совсем пустая страница — слоя нет
    doc.save(str(path))
    doc.close()

    client = _VisionFake(responses=["", ""])  # основной + ретрай
    backend = VlmOcrBackend(PipelineConfig(ocr_dpi=72), client=client)
    parsed = backend.parse(path, tmp_path / "work2")
    assert parsed.meta.get("pages_empty") == [1]


def test_pipeline_reports_coverage_and_partial_status(tmp_path):
    """Обрезка/потери страниц -> coverage_warning и статус partial."""
    from pdftransl.models import ParsedDocument
    from pdftransl.pipeline import TranslationPipeline

    def fake_ru(masked: str) -> str:
        words = {"Content": "Содержимое", "of": "", "the": "",
                 "surviving": "уцелевшей", "page": "страницы"}
        return re.sub(r"[A-Za-z]+", lambda m: words.get(m.group(0), m.group(0)),
                      masked)

    cfg = PipelineConfig(
        db_path=str(tmp_path / "db.sqlite"), output_dir=str(tmp_path / "out"),
        review=False, doc_summary=False, auto_glossary=False, learn=False,
        export_formats=[], embedder="hashing",
    )
    pipe = TranslationPipeline(cfg, client=FakeLLMClient(transform=fake_ru))
    parsed = ParsedDocument(
        source_path="x.pdf",
        markdown="Content of the surviving page.",
        backend="vlm_ocr",
        meta={"ocr": True, "pages_transcribed": 50, "total_pages": 463,
              "truncated": True, "pages_empty": [2, 4]},
    )
    result = pipe._translate_parsed(
        parsed, tmp_path / "out", "job1", lambda *a: None,
        output_name="doc.ru.md",
    )
    assert "coverage_warning" in result.report
    assert "50" in result.report["coverage_warning"]
    assert result.status == "partial"
    assert result.report["ocr"]["pages_empty"] == [2, 4]


# ---- 1. Порядок фолбэка: текстовый слой раньше OCR -------------------------

def test_fallback_prefers_pymupdf_when_text_layer_healthy(tmp_path, three_page_pdf, monkeypatch):
    """MinerU «упал», текстовый слой здоровый: следующим должен идти
    pymupdf (мгновенная экстракция), а не VLM-OCR."""
    pytest.importorskip("fitz")
    from pdftransl.pipeline import TranslationPipeline

    cfg = PipelineConfig(
        db_path=str(tmp_path / "db.sqlite"), output_dir=str(tmp_path / "out"),
        parser_backend="pymupdf",  # доступный primary, чтобы не зависеть от MinerU
        review=False, doc_summary=False, auto_glossary=False, learn=False,
        export_formats=[], embedder="hashing", use_rag=False,
    )
    pipe = TranslationPipeline(cfg, client=FakeLLMClient())
    # vision-клиент «есть»: важно проверить, что OCR всё равно уходит в хвост
    monkeypatch.setattr(pipe, "_vision_client", lambda: _VisionFake())

    parsed = pipe._parse(three_page_pdf, tmp_path / "work")
    assert parsed.backend == "pymupdf"
    assert "Page 1." in parsed.markdown


# ---- 8. Сломанный MinerU запоминается --------------------------------------

def test_mineru_broken_install_detected(monkeypatch, tmp_path):
    import subprocess as sp

    from pdftransl.parsing.mineru_local import MineruLocalBackend

    monkeypatch.setattr(MineruLocalBackend, "_broken_reason", None)
    monkeypatch.setattr(
        "pdftransl.parsing.mineru_local.shutil.which", lambda n: "/fake/mineru")

    def boom(*a, **kw):
        raise sp.CalledProcessError(
            1, a[0], stderr="...\nModuleNotFoundError: No module named 'torch'\n")

    monkeypatch.setattr("pdftransl.parsing.mineru_local.subprocess.run", boom)

    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    backend = MineruLocalBackend(PipelineConfig())
    from pdftransl.exceptions import ParserError

    with pytest.raises(ParserError, match="mineru\\[core\\]"):
        backend.parse(pdf, tmp_path / "w")
    # установка помечена сломанной: бэкенд выпадает из цепочки без запуска
    assert MineruLocalBackend._broken_reason
    assert backend.available() is False
    monkeypatch.setattr(MineruLocalBackend, "_broken_reason", None)  # чистим
