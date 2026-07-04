"""Legacy Hermes Agent-2 wrapper.

Kept for backward compatibility with run_translator_agent.py. The
``translate(text)`` signature is preserved, but the implementation now
uses the pdftransl engine: formula masking, validation and the repair
loop instead of a bare completion call.
"""

import logging

from pdftransl.config import PipelineConfig
from pdftransl.llm.registry import create_client
from pdftransl.masking import Masker
from pdftransl.parsing.splitter import assemble, split_markdown
from pdftransl.translation.translator import Translator, build_segments

logging.basicConfig(level=logging.INFO)


class TranslatorAgent:
    def __init__(self, provider=None, model=None):
        self.config = PipelineConfig.from_env(use_rag=False, review=False)
        if provider:
            self.config.provider = provider
        if model:
            self.config.model = model
        client = create_client(self.config.provider_config())
        self.translator = Translator(client, self.config)

    def translate(self, text):
        try:
            logging.info("Translation started")
            blocks = split_markdown(text)
            segments = build_segments(
                blocks, Masker(), self.config.chunk_char_budget
            )
            self.translator.translate_segments(segments)
            result = assemble([s.final_text() for s in segments])
            logging.info("Translation finished")
            return result
        except Exception as e:
            logging.error(f"Translation error: {e}")
            return ""
