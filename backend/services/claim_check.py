"""Verifying a Business draft's factual claims against its source + brand rules.

This is the blocking accuracy layer (doc §7, hypothesis test 3). `services/claims.py`
flags which sentences make a checkable claim (regex, cheap, per-render); this module
adds the expensive LLM verdict — binding each claim to evidence in the source — plus
deterministic brand-rule checks. Run ONCE at draft time, stored on Post.claim_check.

Strictness is the whole point: a claim is "confirmed" only if its evidence literally
appears in the source. Even if the model returns "confirmed", we re-check the evidence
against the source ourselves and downgrade to "unconfirmed" when it isn't grounded —
so a hallucinated confirmation can't slip through. Not a legal/truth check; it verifies
"does the source actually say this" and "does this obey the rules the user set".
"""
from __future__ import annotations

import logging
from typing import Optional

from services.caption_generator import CaptionParseError
from services.lead_builder import _loads

log = logging.getLogger(__name__)


VERIFY_SYSTEM_PROMPT = """\
You are a STRICT fact-checker for a social post drafted from a company's own public
source. List each distinct factual claim the DRAFT makes (a statistic, a specific
result, a named fact, a date, a capability).

For each claim decide status:
- "confirmed" ONLY if the exact fact is stated in the SOURCE. Put the supporting
  sentence from the SOURCE (copied verbatim) in "evidence".
- "unconfirmed" if the SOURCE does not clearly state it. Leave "evidence" empty.

NEVER mark a claim confirmed without verbatim support in the SOURCE. When unsure,
use "unconfirmed". Opinions/CTAs/generic phrasing are not claims — skip them.

Return ONLY a JSON array, no markdown:
[{"claim": "...", "status": "confirmed|unconfirmed", "evidence": "..."}]
"""


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _grounded(evidence: str, source_norm: str) -> bool:
    ev = _norm(evidence)
    return bool(ev) and ev in source_norm


async def verify_claims(
    text_provider, *, draft_text: str, source_text: str, text_model: str = "",
) -> list[dict]:
    """Return [{claim, status, evidence}] for the draft's factual claims.

    `confirmed` is re-verified against the source here — the model's own verdict is
    never trusted on its own. Raises CaptionParseError if the model returns nothing
    usable twice.
    """
    system = VERIFY_SYSTEM_PROMPT
    user = f"SOURCE:\n{source_text}\n\nDRAFT:\n{draft_text}"

    async def _call() -> Optional[list]:
        raw, _cit = await text_provider.generate_text(
            model=text_model, system_prompt=system, user_prompt=user, max_tokens=800)
        data = _loads(raw)
        if isinstance(data, dict):                 # tolerate {"claims":[...]}
            for k in ("claims", "items", "results"):
                if isinstance(data.get(k), list):
                    data = data[k]
                    break
        return data if isinstance(data, list) else None

    data = await _call()
    if data is None:
        log.warning("Claim check came back unparseable; retrying once")
        data = await _call()
    if data is None:
        raise CaptionParseError("The model did not return a usable claim check.")

    source_norm = _norm(source_text)
    out: list[dict] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        claim = str(raw.get("claim") or "").strip()
        if not claim:
            continue
        evidence = str(raw.get("evidence") or "").strip()
        status = str(raw.get("status") or "").strip().lower()
        # Trust nothing: "confirmed" survives only if the evidence is really in the source.
        if status == "confirmed" and _grounded(evidence, source_norm):
            out.append({"claim": claim, "status": "confirmed", "evidence": evidence})
        else:
            out.append({"claim": claim, "status": "unconfirmed", "evidence": ""})
    return out


def apply_brand_rules(text: str, rules: Optional[dict]) -> dict:
    """Deterministic brand-rule check: forbidden phrases present, required disclaimers
    missing. `rules` = {"forbidden": [...], "required_disclaimers": [...]} or None."""
    low = (text or "").lower()
    forbidden_rules = (rules or {}).get("forbidden") or []
    required = (rules or {}).get("required_disclaimers") or []
    forbidden = [p for p in forbidden_rules if str(p).strip() and str(p).lower() in low]
    missing = [d for d in required if str(d).strip() and str(d).lower() not in low]
    return {"forbidden": forbidden, "missing_disclaimers": missing}
