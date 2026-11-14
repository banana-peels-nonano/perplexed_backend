"""Step 5: the fake provider behaves as tests need, the Claude request is shaped as measured,
and costs are priced correctly. No network: the Claude client is replaced by a stub."""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.llm.anthropic_provider import AnthropicProvider, build_request
from app.llm.base import Message, Prompt, ProviderError, Usage
from app.llm.fake import FakeProvider
from app.llm.pricing import cost_usd

PROMPT = Prompt(
    system="rules + persona",
    history=[Message("user", "q1"), Message("assistant", "a1")],
    final_user="evidence + q2",
)
SCHEMA = {"type": "object"}


async def test_fake_replays_its_script_then_falls_back_to_defaults_and_records_calls():
    boom = ProviderError("simulated timeout", retryable=True)
    fake = FakeProvider(answer=[{"reply": "first", "cited": []}, boom])
    first = await fake.complete(purpose="answer", model="m", prompt=PROMPT, schema=SCHEMA,
                                max_tokens=100)  # fmt: skip
    assert first.data["reply"] == "first"
    with pytest.raises(ProviderError):
        await fake.complete(purpose="answer", model="m", prompt=PROMPT, schema=SCHEMA,
                            max_tokens=100)  # fmt: skip
    third = await fake.complete(purpose="answer", model="m", prompt=PROMPT, schema=SCHEMA,
                                max_tokens=100)  # fmt: skip
    assert third.data["cited"] == []  # default reply once the script runs out
    assert len(fake.calls_for("answer")) == 3
    assert fake.calls[0].prompt.final_user == "evidence + q2"


def test_claude_request_matches_the_measured_settings():
    req = build_request(model="claude-haiku-5-5", prompt=PROMPT, schema=SCHEMA, max_tokens=500)
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    # second cache breakpoint on the LAST history message, none on the new turn
    assert "cache_control" not in req["messages"][0]["content"][0]
    assert req["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["messages"][-1] == {
        "role": "user",
        "content": [{"type": "text", "text": "evidence + q2"}],
    }
    assert req["thinking"] == {"type": "disabled"}
    assert req["output_config"]["effort"] == "low"
    assert req["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}


async def test_claude_provider_parses_reply_and_usage_from_a_stub_client():
    reply = {"reply": "Revenue fell.", "cited": ["A-FIN-001#1"]}
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(reply))],
        usage=SimpleNamespace(input_tokens=300, output_tokens=40,
                              cache_read_input_tokens=2200, cache_creation_input_tokens=0),
        _request_id="req_123",
    )  # fmt: skip
    sent = {}

    async def create(**kwargs):
        sent.update(kwargs)
        return response

    provider = AnthropicProvider(client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    result = await provider.complete(purpose="answer", model="claude-haiku-5-5", prompt=PROMPT,
                                     schema=SCHEMA, max_tokens=500)  # fmt: skip
    assert result.data == reply
    assert result.usage == Usage(input_tokens=300, cache_read_tokens=2200, output_tokens=40)
    assert result.request_id == "req_123"
    assert sent["model"] == "claude-haiku-5-5"


def test_pricing_matches_the_probe_rates():
    # 1M uncached in + 1M out + 1M cache read on Haiku 5.5 = 0.10 + 0.50 + 0.01
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000)
    assert cost_usd("claude-haiku-5-5", usage) == Decimal("0.610000")
    assert cost_usd("fake-model", usage) == Decimal("0")
