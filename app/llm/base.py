"""The one interface the rest of the backend uses to talk to a language model.

Interview code builds a provider-neutral `Prompt` and calls `provider.complete(...)`.
Which model answers (Haiku, Gemini, or the fake used in tests) is decided by whoever
constructs the provider, never by interview code. That's what makes the fake possible
in tests and the Gemini fallback a configuration change rather than a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Message:
    role: str  # 'user' | 'assistant'
    content: str


@dataclass(frozen=True)
class Prompt:
    # Identical for every team talking to this stakeholder -> first cache breakpoint.
    system: str
    # Earlier turns of THIS team's conversation (questions and answers only, no evidence).
    # Stable from one question to the next -> second cache breakpoint at its end.
    history: list[Message] = field(default_factory=list)
    # This turn: attitude + authorised evidence + the question. Changes every time.
    final_user: str = ""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0  # uncached input
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LlmResult:
    data: dict[str, Any]  # the JSON object the model returned, already parsed
    usage: Usage
    provider: str
    model: str
    latency_ms: int
    request_id: str | None = None


class ProviderError(Exception):
    """The call failed. `retryable` says whether trying again later could help
    (timeouts, 429 rate limits, 5xx/529 overload) or not (bad request, bad key)."""

    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class LlmProvider(Protocol):
    name: str

    async def complete(
        self,
        *,
        purpose: str,  # 'classify' | 'answer' (for logs and the fake)
        model: str,
        prompt: Prompt,
        schema: dict[str, Any],  # JSON Schema the reply must match
        max_tokens: int,
    ) -> LlmResult: ...
