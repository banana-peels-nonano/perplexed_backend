"""A stand-in model for tests and load tests.

- Replies come from scripted queues, one per purpose, so tests are exact and repeatable.
- A queued Exception is raised instead of replying, to test failure handling.
- Every call is recorded, so tests can inspect exactly what the backend sent.
- An optional delay imitates real latency for load tests.
When a queue is empty it returns a harmless default.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from app.llm.base import LlmResult, Prompt, Usage

FakeReply = dict[str, Any] | Exception

DEFAULTS: dict[str, dict[str, Any]] = {
    "classify": {"label": "specific", "topics": [], "respectful": True},
    "answer": {"reply": "I'd need to check that and get back to you.", "cited": []},
}


@dataclass
class FakeCall:
    purpose: str
    model: str
    prompt: Prompt
    schema: dict[str, Any]
    max_tokens: int


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        *,
        classify: list[FakeReply] | None = None,
        answer: list[FakeReply] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.queues: dict[str, list[FakeReply]] = {
            "classify": list(classify or []),
            "answer": list(answer or []),
        }
        self.delay = delay
        self.calls: list[FakeCall] = []

    async def complete(
        self,
        *,
        purpose: str,
        model: str,
        prompt: Prompt,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> LlmResult:
        self.calls.append(FakeCall(purpose, model, prompt, schema, max_tokens))
        if self.delay:
            await asyncio.sleep(self.delay)
        queue = self.queues.setdefault(purpose, [])
        reply = queue.pop(0) if queue else DEFAULTS.get(purpose, {})
        if isinstance(reply, Exception):
            raise reply
        sent = prompt.system + "".join(m.content for m in prompt.history) + prompt.final_user
        return LlmResult(
            data=reply,
            usage=Usage(input_tokens=len(sent) // 4, output_tokens=len(json.dumps(reply)) // 4),
            provider=self.name,
            model=model,
            latency_ms=int(self.delay * 1000),
        )

    def calls_for(self, purpose: str) -> list[FakeCall]:
        return [c for c in self.calls if c.purpose == purpose]
