"""Deterministic fake client for tests and dry runs."""

from __future__ import annotations

from typing import Callable, Optional

from pdftransl.llm.base import BaseLLMClient, Message


class FakeLLMClient(BaseLLMClient):
    """Returns canned responses or applies a transform to the last user
    message. Used in unit tests and `--dry-run` style debugging."""

    model = "fake"

    def __init__(
        self,
        responses: Optional[list[str]] = None,
        transform: Optional[Callable[[str], str]] = None,
    ):
        self.responses = list(responses or [])
        self.transform = transform
        self.calls: list[list[Message]] = []
        self.last_response_format: Optional[dict] = None

    def chat(
        self,
        messages: list[Message],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> str:
        self.calls.append(messages)
        self.last_response_format = response_format
        if self.responses:
            return self.responses.pop(0)
        last_user = next(
            (m for m in reversed(messages) if m["role"] == "user"), None
        )
        text = ""
        if last_user is not None:
            content = last_user["content"]
            text = content if isinstance(content, str) else str(content)
        if self.transform:
            return self.transform(text)
        return text
