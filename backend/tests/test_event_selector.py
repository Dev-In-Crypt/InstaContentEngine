"""Event selection rules (Phase 1) — the заготовка of hypothesis test 2 (doc §14).

Explainable rules only. The duplicate anti-rule is a mutation target: dropping it
lets a re-seen item slip through as worthy/weak, so the duplicate test would fail.
"""
from services.event_selector import score_item
from services.sources.base import FetchedItem


def _item(title, body="", raw=None):
    return FetchedItem(external_id="x", kind="rss", title=title, url="u",
                       published_at=None, body=body, raw=raw or {})


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


# --- Precision fixes from hypothesis test 2 (dev pre-releases + fixes-only patches) ---

def test_dev_prerelease_canary_is_weak():
    # A canary build with real impact words is STILL demoted — companies don't
    # post about nightly channels. Mutation guard: drop the dev-prerelease rule
    # and this becomes worthy.
    strength, reason = score_item(
        _item("webframework v16.3.0-canary.90", "Add support for new caching limits"), [])
    assert strength == "weak"
    assert "pre-release" in reason


def test_dev_prerelease_alpha_via_github_tag_is_weak():
    # GitHub-style: the channel lives in raw["tag_name"], not the title.
    strength, _ = score_item(
        _item("Release", "Add support for new resources",
              raw={"tag_name": "v22.4.0-alpha.4"}), [])
    assert strength == "weak"


def test_milestone_prerelease_beta_stays_worthy():
    # beta / rc / preview get announced → they remain worthy.
    strength, _ = score_item(_item("OurApp v2.0.0-beta.1 is now available"), [])
    assert strength == "worthy"


def test_milestone_prerelease_preview_stays_worthy():
    strength, _ = score_item(_item("Platform v3.1.0-preview.6 announced"), [])
    assert strength == "worthy"


def test_fixes_only_patch_is_weak():
    # A semver patch riding only the generic "support for" keyword is churn.
    # Mutation guard: drop the patch anti-rule → this becomes worthy.
    strength, reason = score_item(_item("sdk v1.2.3", "Add support for a new endpoint"), [])
    assert strength == "weak"
    assert "patch release" in reason


def test_security_patch_stays_worthy():
    # Security is the one thing that keeps a patch worthy on its own.
    strength, _ = score_item(
        _item("sdk v1.2.3", "Fixes a security vulnerability (CVE-2026-0001)"), [])
    assert strength == "worthy"


def test_patch_with_concrete_number_stays_worthy():
    strength, _ = score_item(_item("sdk v1.2.3", "Reduces cold start by 40%"), [])
    assert strength == "worthy"


def test_stable_minor_with_features_stays_worthy():
    # Regression: a stable minor (patch == 0) with features is untouched.
    strength, _ = score_item(_item("sdk v1.3.0", "Add support for webhooks and audit logs"), [])
    assert strength == "worthy"
