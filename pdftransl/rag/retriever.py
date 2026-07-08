"""Сборка RAG-контекста для сегмента: точное совпадение из TM ->
похожие примеры -> термины глоссария, найденные в тексте.
"""

from __future__ import annotations

from typing import Optional

from pdftransl.config import PipelineConfig
from pdftransl.rag.glossary import Glossary
from pdftransl.rag.store import TranslationMemory


class RAGContextBuilder:
    def __init__(
        self,
        config: PipelineConfig,
        tm: Optional[TranslationMemory] = None,
        glossary: Optional[Glossary] = None,
    ):
        self.config = config
        self.tm = tm
        self.glossary = glossary

    def build(self, source_text: str) -> dict:
        cfg = self.config
        context: dict = {"tm_examples": [], "glossary_hits": [], "exact_match": None}
        if self.tm is not None:
            exact = self.tm.exact_match(source_text, cfg.source_lang, cfg.target_lang)
            if exact:
                context["exact_match"] = exact
                return context
            context["tm_examples"] = self.tm.search(
                source_text,
                cfg.source_lang,
                cfg.target_lang,
                top_k=cfg.tm_top_k,
                min_similarity=cfg.tm_min_similarity,
                domain=cfg.tm_domain,
            )
        if self.glossary is not None:
            context["glossary_hits"] = self.glossary.match(
                source_text, cfg.source_lang, cfg.target_lang
            )
        return context
