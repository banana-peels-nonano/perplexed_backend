"""How a question moves hidden trust, and when a stakeholder walks out to HR.

Defaults below; organisers override any of them per event in event.config
("trust_rules", "hr"). They are placeholders until the seniors confirm the real numbers.

Trust (0-100, starts at event.config["trust_start"]) moves once per question, from the
classifier's verdict on that question:

    specific +4   broad +2   vague 0   repetitive -2   off_topic -2
    hostile -10   prompt_attack -10   and -5 more if the tone was disrespectful
    capped per question at +6 / -15

So five sharp, polite questions take a stakeholder from 50 (neutral) to 70 (open);
two hostile ones take them from 50 to 30 (guarded).

HR: hostile and prompt_attack questions are strikes against that stakeholder. The
second-to-last strike makes them warn the team (in character); the last one ends the
interview: the question is still charged, the stakeholder files an HR complaint, and
won't talk to that team for `lockout_seconds`. When the lockout ends, strikes reset.
The lockout is visible on purpose: it is a consequence of the team's own conduct, not a
clue about hidden content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_TRUST_RULES: dict[str, Any] = {
    "deltas": {
        "specific": 4,
        "broad": 2,
        "vague": 0,
        "repetitive": -2,
        "off_topic": -2,
        "hostile": -10,
        "prompt_attack": -10,
    },
    "disrespect_penalty": 5,
    "max_gain": 6,
    "max_loss": 15,
}
DEFAULT_HR: dict[str, Any] = {
    "strike_labels": ["hostile", "prompt_attack"],
    "strikes_to_lockout": 3,
    "lockout_seconds": 300,
}


@dataclass(frozen=True)
class Verdict:
    delta: int  # change to trust from this question
    strike: bool  # counts towards an HR complaint
    warn: bool  # one strike short of a complaint: the stakeholder says so
    lockout: bool  # this question ends the interview


def _merged(default: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    out = {**default, **(override or {})}
    if "deltas" in default:
        out["deltas"] = {**default["deltas"], **(override or {}).get("deltas", {})}
    return out


def rules(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The event's trust and HR rules, with defaults for anything it doesn't set."""
    return (
        _merged(DEFAULT_TRUST_RULES, config.get("trust_rules")),
        _merged(DEFAULT_HR, config.get("hr")),
    )


def judge(label: str, respectful: bool, strikes_so_far: int, config: dict[str, Any]) -> Verdict:
    trust_rules, hr = rules(config)
    delta = trust_rules["deltas"].get(label, 0)
    if not respectful:
        delta -= trust_rules["disrespect_penalty"]
    delta = max(-trust_rules["max_loss"], min(trust_rules["max_gain"], delta))
    strike = label in hr["strike_labels"]
    strikes = strikes_so_far + strike
    limit = hr["strikes_to_lockout"]
    return Verdict(
        delta=delta,
        strike=strike,
        warn=strike and strikes == limit - 1,
        lockout=strike and strikes >= limit,
    )


def clamp(trust: int) -> int:
    return max(0, min(100, trust))
