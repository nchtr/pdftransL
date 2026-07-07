"""Provider config -> client instance."""

from __future__ import annotations

from pdftransl.config import ProviderConfig
from pdftransl.llm.anthropic_client import AnthropicClient
from pdftransl.llm.base import BaseLLMClient
from pdftransl.llm.openai_compat import OpenAICompatClient


def create_client(
    config: ProviderConfig, rate_limiter=None, cooldown_gate=None
) -> BaseLLMClient:
    if config.kind == "anthropic":
        return AnthropicClient(config)
    return OpenAICompatClient(
        config, rate_limiter=rate_limiter, cooldown_gate=cooldown_gate
    )
