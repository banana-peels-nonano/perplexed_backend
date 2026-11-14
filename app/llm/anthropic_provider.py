"""Claude via the official SDK (async client).

Request settings are the ones the cost probe measured on 14 Nov:
  - system prompt marked for caching (shared by every team talking to this stakeholder),
  - a second cache breakpoint at the end of the team's history,
  - output_config.effort = "low", thinking disabled (D-011),
  - structured output: the reply must match the JSON schema we pass.
"""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic

from app.llm.base import LlmResult, Prompt, ProviderError, Usage

CACHE = {"type": "ephemeral"}


def build_request(
    *, model: str, prompt: Prompt, schema: dict[str, Any], max_tokens: int
) -> dict[str, Any]:
    """The exact keyword arguments for messages.create. Separate so tests can check it."""
    messages: list[dict[str, Any]] = []
    for i, m in enumerate(prompt.history):
        block: dict[str, Any] = {"type": "text", "text": m.content}
        if i == len(prompt.history) - 1:
            block["cache_control"] = CACHE  # everything up to here is reused next turn
        messages.append({"role": m.role, "content": [block]})
    messages.append({"role": "user", "content": [{"type": "text", "text": prompt.final_user}]})
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": prompt.system, "cache_control": CACHE}],
        "messages": messages,
        "thinking": {"type": "disabled"},
        "output_config": {
            "effort": "low",
            "format": {"type": "json_schema", "schema": schema},
        },
    }


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, *, client: Any = None, timeout: float = 30.0):
        # max_retries=2: the SDK retries timeouts, 429 and 5xx with backoff and honours
        # retry-after. Our own code adds no extra retry loop on top.
        self.client = client or anthropic.AsyncAnthropic(
            api_key=api_key, max_retries=2, timeout=timeout
        )

    async def complete(
        self,
        *,
        purpose: str,
        model: str,
        prompt: Prompt,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> LlmResult:
        request = build_request(model=model, prompt=prompt, schema=schema, max_tokens=max_tokens)
        started = time.perf_counter()
        try:
            response = await self.client.messages.create(**request)
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}", retryable=True) from exc
        except anthropic.APIStatusError as exc:  # includes RateLimitError, Overloaded, 5xx
            retryable = exc.status_code == 429 or exc.status_code >= 500
            raise ProviderError(f"HTTP {exc.status_code}: {exc}", retryable=retryable) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"model returned non-JSON: {text[:200]!r}", retryable=True) from exc

        u = response.usage
        return LlmResult(
            data=data,
            usage=Usage(
                input_tokens=u.input_tokens or 0,
                cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                output_tokens=u.output_tokens or 0,
            ),
            provider=self.name,
            model=model,
            latency_ms=latency_ms,
            request_id=getattr(response, "_request_id", None),
        )
