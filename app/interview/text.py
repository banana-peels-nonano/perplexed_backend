"""Text normalisation shared by the leak check and the grounding check."""

from __future__ import annotations

import re

_UNITS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_U = "|".join(_UNITS[1:10])
_T = "|".join(_TENS)


def normalise(text: str) -> str:
    """The form fingerprints are compared in: case, spelling and punctuation flattened.

    Step 7: haiku wrote "nine percent operating margin" for the fingerprint
    "9 percent operating margin", and "fifteen percent" for "15 percent". Matching the
    literal string missed both. Number words up to ninety-nine become digits, and the
    common spellings of percent, crore, thousands separators, hyphens and curly quotes
    are made the same, so the fingerprint and the reply meet in one form.
    """
    t = text.casefold().replace("\u2019", "'").replace("\u2018", "'")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 3,050 -> 3050
    t = re.sub(r"(\w)-(\w)", r"\1 \2", t)  # twenty-five, 1-in-20
    t = re.sub(
        rf"\b({_T})\s+({_U})\b",
        lambda m: str(10 * (_TENS.index(m[1]) + 2) + _UNITS.index(m[2])),
        t,
    )
    t = re.sub(rf"\b({_T})\b", lambda m: str(10 * (_TENS.index(m[1]) + 2)), t)
    t = re.sub(r"\b(" + "|".join(_UNITS) + r")\b", lambda m: str(_UNITS.index(m[1])), t)
    t = re.sub(r"\s*%", " percent", t)
    t = re.sub(r"\bper\s+cent\b|\bpct\b", "percent", t)
    t = re.sub(r"\b(crores|cr)\b\.?", "crore", t)
    return re.sub(r"\s+", " ", t)


def mentions(text: str, fingerprint: str) -> bool:
    """True if `text` contains `fingerprint` as whole words, after both are normalised.

    Whole words, so "19 percent" or "1.15 percent" never count as "9 percent"/"15 percent".
    """
    fp = re.escape(normalise(fingerprint).strip())
    return re.search(rf"(?<![\w.]){fp}(?!\w)", normalise(text)) is not None
