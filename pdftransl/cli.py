"""Интерфейс командной строки.

    pdftransl translate data/input/article.pdf --provider ollama
    pdftransl translate-md article.md -o article.ru.md
    pdftransl parse data/input/article.pdf -o data/output
    pdftransl glossary add "attention" "внимание"
    pdftransl glossary import terms.csv
    pdftransl tm stats
    pdftransl jobs
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pdftransl.config import PROVIDER_PRESETS, PipelineConfig
from pdftransl.rag.glossary import Glossary
from pdftransl.service import TranslationService


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", default=None,
                        help=f"LLM provider: {', '.join(sorted(PROVIDER_PRESETS))} or custom")
    parser.add_argument("--model", default=None, help="model name override")
    parser.add_argument("--base-url", default=None, help="custom OpenAI-compatible endpoint")
    parser.add_argument("--source-lang", default=None)
    parser.add_argument("--target-lang", default=None)
    parser.add_argument("--backend", default=None,
                        help="parser backend: auto|mineru_local|mineru_api|nougat|"
                             "marker|docling|grobid|vlm_ocr|pymupdf")
    parser.add_argument("--vision-provider", default=None,
                        help="provider for VLM OCR / figure description (defaults to --provider)")
    parser.add_argument("--vision-model", default=None,
                        help="vision model name (e.g. qwen2.5-vl for local OCR)")
    parser.add_argument("--no-ocr", action="store_true",
                        help="do not auto-route detected scans to VLM OCR")
    parser.add_argument("--no-rag", action="store_true", help="disable translation memory / RAG")
    parser.add_argument("--no-review", action="store_true", help="disable LLM review pass")
    parser.add_argument("--no-learn", action="store_true", help="do not store results into TM")
    parser.add_argument("--describe-figures", action="store_true",
                        help="describe exported figures with a VLM")
    parser.add_argument("--db", default=None, help="path to the sqlite database")
    parser.add_argument("--formats", default=None,
                        help="export formats, comma-separated: html,docx,pdf")
    parser.add_argument("--bilingual", action="store_true",
                        help="also produce an alternating source/translation document")
    parser.add_argument("--workers", type=int, default=None,
                        help="parallel segment translations (default 4)")
    parser.add_argument("--fallback", default=None,
                        help="fallback providers, comma-separated (tried on failure)")
    parser.add_argument("--no-cache", action="store_true", help="disable the parse cache")
    parser.add_argument("--no-resume", action="store_true",
                        help="do not resume finished segments from a previous run")
    parser.add_argument("--tm-autoexport-every", type=int, default=None,
                        help="export a fine-tune dataset every N new TM segments")
    parser.add_argument("--backtranslation", action="store_true",
                        help="enable the back-translation semantic check")
    parser.add_argument("--domain", default=None,
                        help="translation-memory domain tag (e.g. physics, ml)")
    parser.add_argument("--score", action="store_true",
                        help="LLM-judge quality score (0-100) per segment")
    parser.add_argument("--render-check", action="store_true",
                        help="render exported HTML and count KaTeX errors")
    parser.add_argument("--no-fix-latex", action="store_true",
                        help="do not LLM-repair broken formulas in the result")
    parser.add_argument("--rpm", type=int, default=None,
                        help="max LLM requests per minute (free-tier throttle)")
    parser.add_argument("--structured", action="store_true",
                        help="use JSON mode for review/glossary calls (OpenAI-compatible)")


def _config_from_args(args: argparse.Namespace) -> PipelineConfig:
    overrides = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.model:
        overrides["model"] = args.model
    if args.base_url:
        overrides["base_url"] = args.base_url
    if args.source_lang:
        overrides["source_lang"] = args.source_lang
    if args.target_lang:
        overrides["target_lang"] = args.target_lang
    if args.backend:
        overrides["parser_backend"] = args.backend
    if args.vision_provider:
        overrides["vision_provider"] = args.vision_provider
    if args.vision_model:
        overrides["vision_model"] = args.vision_model
    if args.no_ocr:
        overrides["ocr_on_scan"] = False
    if args.no_rag:
        overrides["use_rag"] = False
    if args.no_review:
        overrides["review"] = False
    if args.no_learn:
        overrides["learn"] = False
    if args.no_resume:
        overrides["resume"] = False
    if args.tm_autoexport_every:
        overrides["tm_autoexport_every"] = args.tm_autoexport_every
    if args.describe_figures:
        overrides["describe_figures"] = True
    if args.db:
        overrides["db_path"] = args.db
    if args.formats:
        overrides["export_formats"] = [f.strip() for f in args.formats.split(",") if f.strip()]
    if args.bilingual:
        overrides["bilingual"] = True
    if args.workers:
        overrides["max_workers"] = args.workers
    if args.fallback:
        overrides["fallback_providers"] = [p.strip() for p in args.fallback.split(",") if p.strip()]
    if args.no_cache:
        overrides["parse_cache"] = False
    if args.backtranslation:
        overrides["backtranslation_check"] = True
    if args.domain:
        overrides["tm_domain"] = args.domain
    if args.score:
        overrides["quality_score"] = True
    if args.render_check:
        overrides["render_check"] = True
    if args.no_fix_latex:
        overrides["fix_latex"] = False
    if args.rpm:
        overrides["rpm_limit"] = args.rpm
    if args.structured:
        overrides["structured_outputs"] = True
    return PipelineConfig.from_env(**overrides)


def main(argv: list[str] | None = None) -> int:
    from pdftransl.logging_setup import setup_logging

    setup_logging()   # PDFTRANSL_LOG_LEVEL / PDFTRANSL_LOG_FILE
    parser = argparse.ArgumentParser(
        prog="pdftransl", description="Scientific PDF translation engine"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_tr = sub.add_parser("translate", help="translate a PDF end-to-end")
    p_tr.add_argument("pdf", help="path to the PDF file")
    p_tr.add_argument("-o", "--output-dir", default=None)
    _add_common(p_tr)

    p_md = sub.add_parser("translate-md", help="translate an existing markdown file")
    p_md.add_argument("markdown", help="path to the .md file")
    p_md.add_argument("-o", "--output", default=None, help="output .md path")
    _add_common(p_md)

    p_parse = sub.add_parser("parse", help="parse a PDF to markdown (no translation)")
    p_parse.add_argument("pdf")
    p_parse.add_argument("-o", "--output-dir", default="data/output")
    _add_common(p_parse)

    p_gl = sub.add_parser("glossary", help="manage the terminology glossary")
    gl_sub = p_gl.add_subparsers(dest="gl_command", required=True)
    g_add = gl_sub.add_parser("add")
    g_add.add_argument("term")
    g_add.add_argument("translation")
    _add_common(g_add)
    g_imp = gl_sub.add_parser("import")
    g_imp.add_argument("csv", help="CSV file: term,translation[,notes]")
    _add_common(g_imp)
    g_list = gl_sub.add_parser("list")
    _add_common(g_list)

    p_tm = sub.add_parser("tm", help="translation memory operations")
    tm_sub = p_tm.add_subparsers(dest="tm_command", required=True)
    t_stats = tm_sub.add_parser("stats")
    _add_common(t_stats)
    t_exp = tm_sub.add_parser("export")
    t_exp.add_argument("path", help="output .jsonl path")
    _add_common(t_exp)

    p_jobs = sub.add_parser("jobs", help="list translation jobs")
    _add_common(p_jobs)

    p_eng = sub.add_parser("engines", help="show available export engines")
    _add_common(p_eng)

    args = parser.parse_args(argv)
    config = _config_from_args(args)

    if args.command == "translate":
        service = TranslationService(config)
        result = service.translate(args.pdf, args.output_dir)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.status != "failed" else 1

    if args.command == "translate-md":
        from pdftransl.pipeline import TranslationPipeline

        md_path = Path(args.markdown)
        output = Path(args.output) if args.output else md_path.with_suffix(
            f".{config.target_lang}.md"
        )
        pipeline = TranslationPipeline(config)
        result = pipeline.translate_markdown(
            md_path.read_text(encoding="utf-8"), output
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.status != "failed" else 1

    if args.command == "parse":
        from pdftransl.parsing.base import parse_pdf

        parsed = parse_pdf(args.pdf, args.output_dir, config)
        print(json.dumps(
            {
                "markdown_path": parsed.markdown_path,
                "backend": parsed.backend,
                "assets": [a.rel_path for a in parsed.assets],
                "chars": len(parsed.markdown),
            },
            ensure_ascii=False, indent=2,
        ))
        return 0

    if args.command == "glossary":
        glossary = Glossary(config.db_path)
        if args.gl_command == "add":
            glossary.add(args.term, args.translation,
                         config.source_lang, config.target_lang)
            print("added")
        elif args.gl_command == "import":
            n = glossary.load_csv(args.csv, config.source_lang, config.target_lang)
            print(f"imported {n} terms")
        else:
            for row in glossary.list_all():
                print(f"{row['term']}\t{row['translation']}\t"
                      f"[{row['src_lang']}->{row['tgt_lang']}]")
        return 0

    if args.command == "tm":
        service = TranslationService(config)
        if args.tm_command == "stats":
            print(json.dumps(service.tm_stats(), indent=2))
        else:
            n = service._tm().export_jsonl(args.path)
            print(f"exported {n} segments to {args.path}")
        return 0

    if args.command == "jobs":
        service = TranslationService(config)
        for job in service.list_jobs():
            print(f"{job['id']}\t{job['status']}\t{job.get('stage') or '-'}\t"
                  f"{job.get('progress') or 0:.0%}\t{job.get('pdf_path')}")
        return 0

    if args.command == "engines":
        from pdftransl.export.exporter import available_engines

        print(json.dumps(available_engines(), indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
