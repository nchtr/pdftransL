"""LLM-клиенты: единый интерфейс, провайдеры, фолбэк, троттлинг.
"""

from pdftransl.llm.base import BaseLLMClient, image_content, text_content
from pdftransl.llm.registry import create_client

__all__ = ["BaseLLMClient", "create_client", "image_content", "text_content"]
