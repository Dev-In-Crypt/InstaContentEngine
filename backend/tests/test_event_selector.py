"""Event selection rules (Phase 1) — the заготовка of hypothesis test 2 (doc §14).

Explainable rules only. The duplicate anti-rule is a mutation target: dropping it
lets a re-seen item slip through as worthy/weak, so the duplicate test would fail.
"""
from services.event_selector import score_item
from services.sources.base import FetchedItem


def _item(title, body=""):
    return FetchedItem(external_id="x", kind="rss", title=title, url="u",
                       published_at=None, body=body)


def test_customer_impact_is_worthy():
    strength, reason = score_item(_item("New pricing: Pro plan is now cheaper"), [])
    assert strength == "worthy"
    assert "customers" in reason


def test_concrete_number_is_worthy():
    strength, _ = score_item(_item("Uptime improved to 99.9% this quarter"), [])
    assert strength == "worthy"


def test_launch_is_worthy():
    strength, _ = score_item(_item("Introducing our new mobile app"), [])
    assert strength == "worthy"


def test_internal_churn_is_weak():
    strength, _ = score_item(_item("chore: bump dependencies and fix a typo in the README"), [])
    assert strength == "weak"


def test_trivial_word_with_customer_impact_stays_worthy():
    # "docs" alone is trivial, but customer-facing pricing wins.
    strength, _ = score_item(_item("docs: document the new pricing tiers and limits"), [])
    assert strength == "worthy"


def test_bland_update_is_weak():
    strength, _ = score_item(_item("Some reflections on our week"), [])
    assert strength == "weak"


def test_duplicate_against_recent_titles():
    strength, reason = score_item(_item("Launch of v2"), ["launch of v2"])
    assert strength == "duplicate"          # mutation guard: drop the dup rule → fails
    assert "already seen" in reason


def test_duplicate_is_case_and_space_insensitive():
    strength, _ = score_item(_item("  Launch   of  V2 "), ["launch of v2"])
    assert strength == "duplicate"


def test_empty_title_is_weak_not_crash():
    strength, _ = score_item(_item("   "), [])
    assert strength == "weak"
