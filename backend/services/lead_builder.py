"""Turning a source item into a lead card + a ready-to-edit draft.

A lead card is the honest summary of an event: what happened, why it's interesting,
and — crucially — what's *missing* (questions the source doesn't answer). We never
paper over gaps with invented detail; naming them is the point. The draft is then
produced through the normal caption pipeline, grounded strictly in the source.

Plain async functions with the text provider injected — mirrors content_plan, so
it's testable with a stubbed provider. One analysis call + one draft call per item;
the demo caps how many items it runs (cost is bounded by that and the rate limit).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from services.caption_generator import CaptionGenerator, CaptionParseError
from services.claims import find_claims
from services.sources.base import FetchedItem
from models.schemas import Platform

log = logging.getLogger(__name__)

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _loads(raw: str) -> object:
    text = (raw or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        from json_repair import repair_json
        return repair_json(text, return_objects=True)


ANALYSIS_SYSTEM_PROMPT = """\
You analyse ONE update from a company's public source and prepare it for a social
post. You work ONLY from the SOURCE text given — you must not add facts, numbers,
names, or context that aren't in it.

Return ONLY a JSON object:
{{"what_happened": "...", "why_interesting": "...", "missing": ["...", "..."]}}

RULES:
- "what_happened": one plain sentence, only what the source actually says.
- "why_interesting": one sentence on why a customer/reader would care.
- "missing": a list of the concrete details a marketer would want but the source
  does NOT provide (exact numbers, dates, availability, pricing, scope). If the
  source is complete, use an empty list. NEVER invent the missing values — list
  them as open questions.
- No markdown, no commentary, just the JSON object.
"""


async def _analyse(text_provider, item: FetchedItem, text_model: str) -> dict:
    system = ANALYSIS_SYSTEM_PROMPT
    user = f"SOURCE TITLE: {item.title}\nSOURCE URL: {item.url}\n\nSOURCE TEXT:\n{item.body}"

    async def _call() -> Optional[dict]:
        raw, _cit = await text_provider.generate_text(
            model=text_model, system_prompt=system, user_prompt=user, max_tokens=600)
        data = _loads(raw)
        return data if isinstance(data, dict) else None

    data = await _call()
    if data is None:
        log.warning("Lead analysis came back unparseable; retrying once")
        data = await _call()
    if data is None:
        raise CaptionParseError("The model did not return a usable lead analysis.")

    missing = data.get("missing")
    return {
        "what_happened": str(data.get("what_happened") or item.title).strip(),
        "why_interesting": str(data.get("why_interesting") or "").strip(),
        "missing": [str(m).strip() for m in missing if str(m).strip()]
        if isinstance(missing, list) else [],
    }


def _has_ungrounded_claim(caption_text: str, source_text: str) -> bool:
    """True if the draft asserts a statistic/figure that isn't in the source.

    A заготовка of the blocking accuracy check (doc §14, test 3): the real
    claim-checker lands in Phase 4, but even now a drafted number that never
    appears in the source is exactly the hallucination we must flag, not ship.
    """
    src = (source_text or "").lower()
    for claim in find_claims(caption_text):
        for num in re.findall(r"\d+(?:[.,]\d+)?", claim["text"]):
            if num not in src:
                return True
    return False


async def _draft(
    text_provider, item: FetchedItem, analysis: dict, *, text_model: str,
    platform: Platform, brand_voice: str, niche: Optional[str],
    target_audience: Optional[str],
) -> dict:
    instructions = (
        f"{analysis['why_interesting']} "
        "Write ONLY from the facts in the SOURCE below. Do not invent numbers, "
        "names, dates, or claims; if a detail isn't in the source, leave it out.\n\n"
        f"SOURCE:\n{item.title}\n{item.body}"
    )
    caption = await CaptionGenerator(text_provider).generate(
        topic=analysis["what_happened"] or item.title,
        format="single",
        text_model=text_model,
        additional_instructions=instructions,
        platform=platform,
        brand_voice=brand_voice or None,
        niche=niche,
        target_audience=target_audience,
        web_grounded=False,          # stay grounded in the source, don't pull the web
    )
    source_text = f"{item.title}\n{item.body}"
    return {
        "platform": platform.value if hasattr(platform, "value") else str(platform),
        "hook": caption.hook,
        "caption": caption.caption,
        "cta": caption.cta,
        "hashtags": caption.hashtags,
        "unverified": _has_ungrounded_claim(caption.caption, source_text),
    }


async def build_lead(
    text_provider, item: FetchedItem, *, text_model: str = "",
    platform: Platform = Platform.INSTAGRAM, brand_voice: str = "",
    niche: Optional[str] = None, target_audience: Optional[str] = None,
) -> dict:
    """Build one lead card (what/why/missing) plus one grounded draft for `item`."""
    analysis = await _analyse(text_provider, item, text_model)
    draft = await _draft(
        text_provider, item, analysis, text_model=text_model, platform=platform,
        brand_voice=brand_voice, niche=niche, target_audience=target_audience)
    return {
        "title": item.title,
        "source_url": item.url,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "what_happened": analysis["what_happened"],
        "why_interesting": analysis["why_interesting"],
        "missing": analysis["missing"],
        "drafts": [draft],
    }
