"""Claim verification (Phase 4) — the blocking accuracy layer (doc §14 test 3).

The evidence-grounding guard is the mutation target: even when the model returns
"confirmed", a claim whose evidence isn't literally in the source is downgraded to
"unconfirmed" — a hallucinated confirmation must never survive.
"""
import pytest

from services.claim_check import (
    VERIFY_SYSTEM_PROMPT,
    apply_brand_rules,
    verify_claims,
)


class StubProvider:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def generate_text(self, **kwargs):
        self.calls += 1
        return (self.reply, [])


_SOURCE = "We shipped v2. The build is 50% faster than before."


@pytest.mark.asyncio
async def test_grounded_claim_is_confirmed():
    reply = ('[{"claim": "Builds are 50% faster", "status": "confirmed", '
             '"evidence": "The build is 50% faster than before."}]')
    out = await verify_claims(StubProvider(reply), draft_text="Now 50% faster.",
                              source_text=_SOURCE)
    assert out == [{"claim": "Builds are 50% faster", "status": "confirmed",
                    "evidence": "The build is 50% faster than before."}]


@pytest.mark.asyncio
async def test_fabricated_confirmed_is_downgraded():
    # The model LIES: marks an invented figure "confirmed" with evidence not in source.
    reply = ('[{"claim": "Sales grew 300%", "status": "confirmed", '
             '"evidence": "Sales grew 300% this year"}]')
    out = await verify_claims(StubProvider(reply), draft_text="Sales grew 300%.",
                              source_text=_SOURCE)
    # Mutation guard: without the grounding re-check this would stay "confirmed".
    assert out == [{"claim": "Sales grew 300%", "status": "unconfirmed", "evidence": ""}]


@pytest.mark.asyncio
async def test_model_unconfirmed_stays_unconfirmed():
    reply = '[{"claim": "It is the best tool", "status": "unconfirmed", "evidence": ""}]'
    out = await verify_claims(StubProvider(reply), draft_text="Best tool ever.",
                              source_text=_SOURCE)
    assert out[0]["status"] == "unconfirmed"


def test_verify_prompt_demands_full_claim_grounding():
    # Regression guard for hypothesis test 3: a live run confirmed a claim naming
    # "GitHub Copilot" whose evidence only listed other tools — because the prompt
    # let a claim ride evidence that supported just PART of it. The prompt must now
    # require splitting bundled facts and grounding EVERY named specific.
    low = VERIFY_SYSTEM_PROMPT.lower()
    assert "split" in low
    assert "every" in low and ("named product" in low or "named tool" in low)
    assert "part of it" in low or "only part" in low


@pytest.mark.asyncio
async def test_retries_once_on_unparseable():
    prov = StubProvider("not json")
    # both calls return junk → raises after one retry
    from services.caption_generator import CaptionParseError
    with pytest.raises(CaptionParseError):
        await verify_claims(prov, draft_text="x", source_text=_SOURCE)
    assert prov.calls == 2


def test_apply_brand_rules_flags_forbidden_and_missing():
    rules = {"forbidden": ["guaranteed"], "required_disclaimers": ["not financial advice"]}
    res = apply_brand_rules("Guaranteed returns on your money!", rules)
    assert res["forbidden"] == ["guaranteed"]
    assert res["missing_disclaimers"] == ["not financial advice"]


def test_apply_brand_rules_clean_when_compliant():
    rules = {"forbidden": ["guaranteed"], "required_disclaimers": ["not financial advice"]}
    res = apply_brand_rules("A steady approach. Not financial advice.", rules)
    assert res == {"forbidden": [], "missing_disclaimers": []}


def test_apply_brand_rules_empty_rules():
    assert apply_brand_rules("anything", None) == {"forbidden": [], "missing_disclaimers": []}
