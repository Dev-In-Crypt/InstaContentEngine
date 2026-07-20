"""Flagging the sentences an author should verify before posting under a brand.

Web-grounded models paraphrase real research into confident, specific numbers:
"cuts mortality risk 50-70%", "a Kent University study found effort feels 2-3x
harder". The studies exist; the exact figures are the model rounding in its own
favour. Publishing that under a brand is a reputation risk.

This is NOT a fact checker — the model that invented a figure can't honestly
judge it, and a second model pass is cost with no added trust. It surfaces which
sentences make a checkable numeric or research claim, so the human verifies them
against the sources panel. Numbers are never removed: "8000 steps a day" is the
topic, not an error. Pure functions so the rules are testable.
"""
from __future__ import annotations

import re

#: A percentage: "30%", "50-70 %", "3.5%".
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s?%")
#: Any digit — used only in combination with the statistical language below, so a
#: bare "5 tips" or "20 minutes" doesn't trip the flag.
_HAS_NUMBER = re.compile(r"\d")
#: The vocabulary of a research claim. A number near any of these reads as a
#: statistic rather than an instruction.
_STAT_LANGUAGE = re.compile(
    r"\b(?:study|studies|research|researchers?|scientists?|survey|surveyed|data|"
    r"statistics?|found that|shown to|proven to|according to|on average|"
    r"reduces?|reduced|increases?|increased|lowers?|lowered|boosts?|boosted|"
    r"cuts?|doubles?|triples?)\b",
    re.IGNORECASE,
)
#: "1 in 5", "3 out of 4" — a ratio claim even without stat words.
_RATIO = re.compile(r"\b\d+\s+(?:in|out of)\s+\d+\b", re.IGNORECASE)
#: A named authority a reader would take as evidence.
_AUTHORITY = re.compile(
    r"\b(?:Harvard|Stanford|MIT|Oxford|Cambridge|Yale|Princeton|JAMA|WHO|CDC|NASA|NIH|FDA)\b"
    r"|\b[A-Z][a-zA-Z]+ (?:University|Institute|Journal|Hospital|Clinic)\b"
    r"|\bUniversity of [A-Z][a-zA-Z]+\b",
)

#: Split on sentence enders followed by space + capital / digit / quote. Keeps
#: "e.g." and initials attached in the common cases; perfection isn't needed, a
#: usefully-scoped sentence is.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z0-9])")


def _reason(sentence: str) -> str | None:
    """Why this sentence needs checking, or None if it doesn't."""
    if _PERCENT.search(sentence) or _RATIO.search(sentence):
        return "contains a statistic"
    if _AUTHORITY.search(sentence):
        return "names a source"
    if _HAS_NUMBER.search(sentence) and _STAT_LANGUAGE.search(sentence):
        return "cites research with a number"
    return None


def find_claims(text: str) -> list[dict]:
    """Sentences that make a checkable numeric or research claim, as
    [{"text", "reason"}] in reading order, de-duplicated.

    A bare number ("5 tips", "20 minutes daily") is not flagged: it takes a
    percentage, a ratio, a named authority, or a number sitting next to research
    language for a sentence to count.
    """
    flags: list[dict] = []
    seen: set[str] = set()
    for raw in _SENTENCE_SPLIT.split(text or ""):
        sentence = raw.strip()
        if not sentence:
            continue
        reason = _reason(sentence)
        if reason is None:
            continue
        key = " ".join(sentence.lower().split())
        if key in seen:
            continue
        seen.add(key)
        flags.append({"text": sentence, "reason": reason})
    return flags
