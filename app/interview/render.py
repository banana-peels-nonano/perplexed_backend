"""What a passage looks like at a given mode: one rendering for the model AND the card.

  quote       the authentic text, unchanged.
  paraphrase  the author's `paraphrase` if the case has one, else the authentic text;
              either way every figure, code, date and time [withheld]
              (quarter labels like Q1 stay, they are context, not facts).
  aware/deny  no text at all; callers never ask for it.

Why one function for both: step 7's 10-question run showed paraphrase-mode cards
carrying the exact figures the stakeholder had been told not to say ("9 percent",
"2.1 crore", "412 of 690"). The model talked around them, then the card handed them
over. A team must never see more on a card than the stakeholder was allowed to say.

Why every figure, not only fingerprints: blanking just the fingerprint "RL-2207" left
"[withheld] through RL-2219", and the CFO told the team "the lot that keeps coming up
is RL-2219". Paraphrase means no exact figures at all; that is now literally true.
"""

from __future__ import annotations

import re

from app.content.schema import Case, Mode

WITHHELD = "[withheld]"

_FIGURE = re.compile(
    r"[A-Za-z]*-?\d(?:\w|[.,:](?=\d))*(?:-\w+)*"
    r"(?:\s*(?:%|percent\b|per cent\b|crores?\b|cr\b))?",
    re.IGNORECASE,
)
_KEEP = re.compile(r"^(?:Q[1-4]|H[12])$", re.IGNORECASE)


def render(case: Case, passage_id: str, mode: Mode) -> str:
    passage = case.passage[passage_id]
    if mode == Mode.quote:
        return passage.text
    if mode == Mode.paraphrase:
        # the author's paraphrase when there is one (step 8: phrase fingerprints like
        # "June shipment target" survived blanking and caused regenerations/deflections)
        text = passage.paraphrase or passage.text
        return _FIGURE.sub(lambda m: m[0] if _KEEP.match(m[0]) else WITHHELD, text)
    raise ValueError(f"{passage_id}: a {mode.name} passage has no text to show")
