"""VLM processing of exported figures.

For each exported image a vision model produces a target-language
description (axes, trends, label transcription+translation). Results
are stored in ``figures.json`` next to the output markdown and in
``Asset.description`` — usable for accessibility alt-text, figure
indexes, or RAG over figures.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import BaseLLMClient, vision_message
from pdftransl.models import Asset
from pdftransl.translation.prompts import FIGURE_SYSTEM, lang_name

logger = logging.getLogger(__name__)

# figures that can't be rasterized to a data URL for vision APIs
_SKIP_SUFFIXES = {".svg"}


def describe_figures(
    assets: list[Asset],
    client: BaseLLMClient,
    config: PipelineConfig,
    output_json: str | Path | None = None,
) -> dict[str, str]:
    """Describe figures with a VLM; never raises — figure description
    is an enhancement, not a required stage."""
    if not client.supports_vision:
        logger.warning("Provider %s does not support vision; skipping figures",
                       getattr(client, "model", "?"))
        return {}
    system = FIGURE_SYSTEM.format(tgt=lang_name(config.target_lang))
    descriptions: dict[str, str] = {}
    # only real figures — skip full-page OCR renders exported as "page" assets
    figures = [a for a in assets if a.kind != "page"]
    for asset in figures[: config.max_figures]:
        path = Path(asset.path)
        if not path.exists() or path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        try:
            text = client.chat(
                [
                    {"role": "system", "content": system},
                    vision_message("Describe this figure.", path),
                ],
                temperature=0.2,
            )
        except Exception as exc:
            logger.warning("Figure description failed for %s: %s", path.name, exc)
            continue
        asset.description = text.strip()
        descriptions[asset.rel_path or str(path)] = asset.description

    if output_json and descriptions:
        out = Path(output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(descriptions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return descriptions
