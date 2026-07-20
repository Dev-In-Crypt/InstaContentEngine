"""Which sentences get flagged for the author to verify before posting.

Every case is a paraphrase seen in a real generation.
"""
from services.claims import find_claims


def _texts(text):
    return [c["text"] for c in find_claims(text)]


def test_percentage_is_flagged():
    flags = find_claims("Walking cuts mortality risk by 50-70%.")
    assert len(flags) == 1
    assert flags[0]["reason"] == "contains a statistic"


def test_named_authority_is_flagged():
    flags = find_claims("A Kent University study found effort feels harder.")
    assert len(flags) == 1
    assert flags[0]["text"].startswith("A Kent University study")


def test_number_with_research_language_is_flagged():
    assert _texts("Research shows 8000 steps daily reduces depression.") == \
        ["Research shows 8000 steps daily reduces depression."]


def test_ratio_claim_is_flagged():
    assert _texts("Roughly 1 in 5 adults skips breakfast.") == \
        ["Roughly 1 in 5 adults skips breakfast."]


def test_a_bare_instruction_number_is_not_flagged():
    """"20 minutes" is advice, not a statistic — flagging it would cry wolf."""
    assert find_claims("Start with 20 minutes daily and build gradually.") == []


def test_a_list_count_is_not_flagged():
    assert find_claims("Here are 5 tips that changed my routine.") == []


def test_a_motivational_sentence_is_not_flagged():
    assert find_claims("Consistency beats intensity every single time.") == []


def test_only_the_claim_sentence_is_returned():
    text = ("Consistency beats intensity. Studies show it lowers dropout by 40%. "
            "Start today.")
    assert _texts(text) == ["Studies show it lowers dropout by 40%."]


def test_a_repeated_claim_is_deduped():
    """A thread often restates its hook verbatim; show it once."""
    text = "Sleep boosts recovery by 30%. Sleep boosts recovery by 30%."
    assert len(find_claims(text)) == 1


def test_empty_text_is_no_claims():
    assert find_claims("") == []
    assert find_claims(None) == []
