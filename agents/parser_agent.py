"""Legacy Hermes Agent-1 wrapper.

Kept for backward compatibility with run_parser_agent.py / Hermes
orchestration. Delegates to the pdftransl parsing backends (local
MinerU -> MinerU API -> PyMuPDF fallback).
"""

import logging

from pdftransl.config import PipelineConfig
from pdftransl.parsing.base import parse_pdf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ParserAgent:

    def run(self, pdf_path, output_dir):
        try:
            logging.info("Parsing started")
            parsed = parse_pdf(pdf_path, output_dir, PipelineConfig.from_env())
            logging.info("Parsing finished")
            logging.info(f"Markdown found: {parsed.markdown_path}")
            return parsed.markdown_path
        except Exception as e:
            logging.error(f"Parsing error: {e}")
            return None
