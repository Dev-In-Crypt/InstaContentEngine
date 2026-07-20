"""Proposing a week of post topics — the cheap first gate before spending on posts.

Generating a week of content in one shot is the headline value for a social
manager, but a week of junk is wasted money and lost trust. So this doesn't
generate posts: it proposes N *topics*, balanced across the content pillars and
on-brand, in a single cheap LLM call. The user prunes and edits that list, and
only the approved topics are expanded into full posts through the normal
per-post pipeline. Money is spent only on topics a human kept.

One call, plain functions, provider injected — testable with a stub.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from services.caption_generator import CaptionParseError
from services.pillars import DEFAULT_PILLARS, _PILLAR_BY_KEY, classify_pillar

log = logging.getLogger(__name__)

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _loads_plan(raw: str) -> object:
    """Parse the plan reply, repairing near-JSON on failure.

    Unlike caption parsing (a single {...} envelope), the plan is a top-level
    ARRAY, so this can't use extract_json — it strips a code fence, then tries a
    strict parse, then json-repair. Repair never raises; a hopeless reply comes
    back as [] / "" and the caller retries.
    """
    text = (raw or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        from json_repair import repair_json
        return repair_json(text, return_objects=True)


def _pillar_table() -> str:
    return "\n".join(
        f"- {p['key']}: {p['label']} (~{p['target_pct']}% of the mix)"
        for p in DEFAULT_PILLARS
    )


PLAN_SYSTEM_PROMPT = """\
Act as a social media content strategist planning a batch of posts for one brand.

BRAND/PERSON CONTEXT:
{brand_voice}

Niche/industry: {niche}
Target audience: {target_audience}
Platform: {platform}
Overall theme for this batch (optional): {theme}

Propose EXACTLY {count} post topics as a JSON array. Balance them across these
content pillars, close to their target shares:
{pillar_table}

HARD RULES:
- Return ONLY a JSON array of objects: [{{"topic": "...", "pillar": "<pillar key>", "angle": "..."}}]
- "topic" is a specific, post-ready title — NOT a vague category. Bad: "Fitness tips".
  Good: "The 3-minute mobility routine to do before you sit at your desk".
- "pillar" MUST be one of the keys above.
- "angle" is one short line on why this resonates with the audience.
- Every topic is DISTINCT — no two restate the same idea in different words.
- Stay on the niche and audience above. Do not invent statistics or study results.
- No markdown, no commentary, just the JSON array.
"""


def _normalise(topic: str) -> str:
    return " ".join((topic or "").lower().split())


def _coerce(items: object, count: int) -> list[dict]:
    """Validate the model's array into clean {topic, pillar, angle}, deduped."""
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        topic = str(raw.get("topic") or "").strip()
        if not topic:
            continue
        key = _normalise(topic)
        if key in seen:                       # a restated duplicate — drop it
            continue
        seen.add(key)
        pillar = str(raw.get("pillar") or "").strip()
        if pillar not in _PILLAR_BY_KEY:      # model gave a bad/blank pillar
            pillar = classify_pillar(topic)
        out.append({
            "topic": topic,
            "pillar": pillar,
            "angle": str(raw.get("angle") or "").strip(),
        })
    return out[:count]


async def plan_topics(
    text_provider,
    *,
    niche: Optional[str],
    target_audience: Optional[str],
    theme: Optional[str],
    platform: str,
    count: int,
    text_model: str = "",
    brand_voice: str = "",
) -> list[dict]:
    """Return up to `count` distinct, pillar-balanced topics as {topic, pillar, angle}.

    Fewer than `count` is accepted — a shorter, on-point list beats padding with
    filler. Raises CaptionParseError if the model gives nothing usable twice.
    """
    system = PLAN_SYSTEM_PROMPT.format(
        brand_voice=brand_voice or "A helpful, credible brand voice.",
        niche=niche or "General",
        target_audience=target_audience or "General audience",
        platform=platform,
        theme=(theme or "").strip() or "None",
        count=count,
        pillar_table=_pillar_table(),
    )
    user = f"Plan {count} posts."

    async def _call() -> list[dict]:
        raw, _citations = await text_provider.generate_text(
            model=text_model, system_prompt=system, user_prompt=user,
            max_tokens=200 * count + 400,
        )
        data = _loads_plan(raw)
        # The array may arrive bare or wrapped as {"topics":[...]} / {"posts":[...]}.
        if isinstance(data, dict):
            for k in ("topics", "posts", "items", "plan"):
                if isinstance(data.get(k), list):
                    data = data[k]
                    break
        return _coerce(data, count)

    items = await _call()
    if not items:
        # Same one-shot retry as caption generation: a fresh sample usually parses.
        log.warning("Topic plan came back empty; retrying once")
        items = await _call()
    if not items:
        raise CaptionParseError("The model did not return any usable topics.")
    return items
