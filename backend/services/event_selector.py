"""Deciding which source items are worth posting about — explainable rules only.

The riskiest part of the Business module is selection: too much junk and a person
stops trusting the feed. So the rules are deliberately simple and legible (doc §5),
never ML — every verdict comes with a one-line reason a human can sanity-check.

A verdict is one of:
- "worthy"    — a real event (customer impact, a concrete result, a launch, a change).
- "weak"      — nothing wrong, but no strong signal; still shown, just flagged.
- "duplicate" — a matching item was already seen recently (caller supplies the window).

Pure functions so the rules are testable and the selection is reproducible.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from services.sources.base import FetchedItem

# Customer-facing impact: price, availability, limits, security — the things a
# reader actually reacts to.
_IMPACT = re.compile(
    r"\b(?:price|pricing|cost|free|plan|tier|limit|quota|deprecat\w*|discontinu\w*|"
    r"sunset|end of life|eol|breaking|security|vulnerab\w*|outage|incident|downtime|"
    r"availab\w*|launch\w*|release[ds]?|now available|general availability|\bga\b|"
    r"acqui\w*|partnership|integration|support for|support[s]? )\b",
    re.IGNORECASE,
)
# A launch/ship signal specifically.
_LAUNCH = re.compile(
    r"\b(?:launch\w*|introduc\w*|announc\w*|releas\w*|ship\w*|now available|"
    r"unveil\w*|rolling out|roll[s]? out)\b", re.IGNORECASE)
# A before→after change with a concrete result.
_CHANGE = re.compile(
    r"\b(?:up to|from\s+\S+\s+to\s+\S+|increas\w*|decreas\w*|reduc\w*|doubl\w*|"
    r"tripl\w*|faster|cheaper|now \d)\b", re.IGNORECASE)
# A quantified result: percentage, currency, multiplier, or ratio.
_NUMBER = re.compile(
    r"\d+(?:[.,]\d+)?\s?%|[$€£]\s?\d|\b\d+(?:[.,]\d+)?x\b|\bx\d+\b|"
    r"\b\d+\s+(?:in|out of)\s+\d+\b", re.IGNORECASE)
# Internal/cosmetic churn — not newsworthy on its own.
_TRIVIAL = re.compile(
    r"\b(?:chore|docs?|typo|readme|refactor\w*|lint\w*|\bci\b|cleanup|"
    r"bump|dependenc\w*|internal|whitespace|formatting|comment[s]?)\b",
    re.IGNORECASE)


def _normalise(title: str) -> str:
    return " ".join((title or "").lower().split())


def score_item(item: FetchedItem, recent_titles: Iterable[str]) -> tuple[str, str]:
    """Classify one item as ("worthy"|"weak"|"duplicate", reason).

    `recent_titles` is whatever the caller considers "already seen" (the poller
    passes the last ~30 days; the demo passes items scored earlier this run).
    """
    title = (item.title or "").strip()
    norm = _normalise(title)
    if not norm:
        return ("weak", "no title to judge")

    if norm in {_normalise(t) for t in recent_titles}:
        return ("duplicate", "a matching item was already seen recently")

    body = (item.body or "").strip()
    text = f"{title}\n{body}"

    # Anti-rule: internal/cosmetic churn with nothing customer-facing.
    if _TRIVIAL.search(title) and not _IMPACT.search(text):
        return ("weak", "looks like an internal or cosmetic change")

    signals: list[str] = []
    if _IMPACT.search(text):
        signals.append("affects customers (price, limits, availability, security)")
    if _NUMBER.search(text):
        signals.append("carries a concrete number or result")
    if _CHANGE.search(text):
        signals.append("describes a before→after change")
    if _LAUNCH.search(text):
        signals.append("a launch or release")

    if signals:
        return ("worthy", "; ".join(dict.fromkeys(signals)))
    return ("weak", "no strong newsworthiness signal")
