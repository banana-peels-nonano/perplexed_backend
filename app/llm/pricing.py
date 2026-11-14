"""USD per million tokens, list prices read on 14 Nov 2026 (re-check before the event).

Used to fill llm_call.cost_usd, so spend can be summed during the event.
"""

from __future__ import annotations

from decimal import Decimal

from app.llm.base import Usage

# model: (input, output, cache_read, cache_write_5m)
PRICES: dict[str, tuple[str, str, str, str]] = {
    "claude-haiku-5-5": ("0.10", "0.50", "0.01", "0.125"),
    "claude-sonnet-5-5": ("2.00", "10.00", "0.10", "2.50"),
    "gemini-3.5-flash-lite": ("0.30", "2.50", "0.03", "0.30"),
}


def cost_usd(model: str, usage: Usage) -> Decimal:
    if model not in PRICES:
        return Decimal("0")  # unknown or fake model: free, and obviously so in reports
    inp, out, cread, cwrite = (Decimal(p) for p in PRICES[model])
    total = (
        usage.input_tokens * inp
        + usage.output_tokens * out
        + usage.cache_read_tokens * cread
        + usage.cache_write_tokens * cwrite
    )
    return (total / Decimal(1_000_000)).quantize(Decimal("0.000001"))
