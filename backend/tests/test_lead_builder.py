"""Lead card + grounded draft (Phase 1) — заготовка of hypothesis test 3 (doc §14).

Uses a stubbed text provider that returns a queued analysis reply then a caption
reply. The "unverified" flag on a figure absent from the source is the mutation
target: removing it lets a hallucinated statistic ship as fact.
"""
import pytest

from services.lead_builder import build_lead, _has_ungrounded_claim
from services.sources.base import FetchedItem


class StubProvider:
    """Returns queued (content, citations) replies, in order, per generate_text call."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def generate_text(self, **kwargs):
        self.calls += 1
        return (self.replies.pop(0), [])


def _item(title="v2 released", body="We shipped v2 with faster builds."):
    return FetchedItem(external_id="x", kind="github_releases", title=title,
                       url="https://ex.com/v2", published_at=None, body=body)


_ANALYSIS = ('{"what_happened": "They shipped v2", "why_interesting": "Faster builds", '
             '"missing": ["exact speed numbers", "release date"]}')


def _caption(caption_text):
    import json
    return json.dumps({
        "caption": caption_text, "hashtags": ["#dev"], "cta": "Try it", "hook": "v2 shipped",
        "image_search_queries": [], "image_gen_prompts": [], "alt_text": "",
    })


@pytest.mark.asyncio
async def test_build_lead_card_and_draft():
    prov = StubProvider([_ANALYSIS, _caption("v2 is here and builds are faster.")])
    lead = await build_lead(prov, _item())
    assert lead["what_happened"] == "They shipped v2"
    assert lead["why_interesting"] == "Faster builds"
    assert lead["missing"] == ["exact speed numbers", "release date"]   # gaps named, not invented
    assert lead["source_url"] == "https://ex.com/v2"
    assert len(lead["drafts"]) == 1
    draft = lead["drafts"][0]
    assert draft["hook"] and draft["caption"]
    assert draft["unverified"] is False        # no figure outside the source


@pytest.mark.asyncio
async def test_build_lead_flags_ungrounded_figure():
    # The draft asserts "300%" — a number that never appears in the source.
    prov = StubProvider([_ANALYSIS, _caption("Sales grew 300% after the v2 launch.")])
    lead = await build_lead(prov, _item(body="We shipped v2 with faster builds."))
    assert lead["drafts"][0]["unverified"] is True   # mutation guard


def test_has_ungrounded_claim_grounded_vs_not():
    assert _has_ungrounded_claim("Now 50% faster.", "The build is 50% faster than before.") is False
    assert _has_ungrounded_claim("Sales grew 300%.", "We had a good quarter.") is True
    assert _has_ungrounded_claim("Five quick tips for you.", "anything") is False   # bare number, no claim


@pytest.mark.asyncio
async def test_build_lead_retries_once_on_unparseable_analysis():
    # First analysis reply is junk (repair yields no dict); retry succeeds.
    prov = StubProvider(["not json at all", _ANALYSIS, _caption("ok")])
    lead = await build_lead(prov, _item())
    assert lead["what_happened"] == "They shipped v2"
    assert prov.calls == 3                     # junk + retry + caption
