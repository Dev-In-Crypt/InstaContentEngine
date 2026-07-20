"""Deterministic cleanup of model output: no markdown, no link dump, no em dash.

Every rule here exists because the prompt asked for it and a live run showed the
model ignoring it anyway.
"""
from services.text_polish import (
    drop_link_only_lines, flatten_dashes, polish, strip_markdown,
)


# ── markdown ────────────────────────────────────────────────────────────────

def test_link_keeps_its_url_when_asked():
    out = strip_markdown("A [JAMA study](https://jama.org/x) found that walking works.")
    assert out == "A JAMA study (https://jama.org/x) found that walking works."


def test_link_drops_its_url_for_instagram():
    """Instagram captions don't make links clickable — the URL is dead text."""
    out = strip_markdown("Per [a JAMA study](https://jama.org/x), walking wins.", keep_urls=False)
    assert out == "Per a JAMA study, walking wins."


def test_emphasis_and_headings_lose_their_markers():
    assert strip_markdown("**Bold** and _italic_ and `code`.") == "Bold and italic and code."
    assert strip_markdown("## Heading\nBody.") == "Heading\nBody."


def test_plain_text_is_untouched():
    """Guard against over-eager regexes: a lone asterisk or underscore is content."""
    for text in ("3 * 4 = 12", "file_name.py and snake_case", "Cost: 5 * 2 dollars",
                 "- first bullet\n- second bullet", "Nothing to strip here."):
        assert strip_markdown(text) == text


# ── dashes ──────────────────────────────────────────────────────────────────

def test_em_dash_becomes_a_comma():
    assert flatten_dashes("Your body can keep going—your brain says stop.") == \
        "Your body can keep going, your brain says stop."


def test_spaced_dash_does_not_leave_double_spaces():
    assert flatten_dashes("Consistency — not intensity — wins.") == \
        "Consistency, not intensity, wins."


def test_numeric_range_keeps_its_dash():
    """"6–12 months" is a value, not punctuation."""
    assert flatten_dashes("Budget on your lowest income in the last 6–12 months.") == \
        "Budget on your lowest income in the last 6–12 months."


def test_text_without_dashes_is_untouched():
    assert flatten_dashes("Plain sentence, nothing to do.") == "Plain sentence, nothing to do."


# ── trailing link dump ──────────────────────────────────────────────────────

def test_trailing_source_lines_are_dropped():
    """What a grounded model appends to the caption even when told not to."""
    text = ("This system is flexible and efficient. What's your go-to tip?\n\n"
            "grabguides.com\n"
            "https://nutritiontofit.com/how-to-meal-prep")
    assert drop_link_only_lines(text) == \
        "This system is flexible and efficient. What's your go-to tip?"


def test_link_inside_a_sentence_survives():
    """Only the tail is trimmed — a cited source mid-text is content."""
    text = ("Walking 7,000 steps cuts mortality risk (https://jama.org/x).\n"
            "That is the whole argument.")
    assert drop_link_only_lines(text) == text


def test_last_line_with_prose_around_a_link_survives():
    """Only lines that are NOTHING but links go. A sentence that happens to carry
    a source is the author's text."""
    text = ("Start with 20 minutes daily.\n"
            "Full write-up here: https://example.com/guide, worth a read.")
    assert drop_link_only_lines(text) == text


def test_a_normal_last_paragraph_survives():
    text = "Start with 20 minutes daily.\n\nWhat is your first step?"
    assert drop_link_only_lines(text) == text


# ── the composed entry point ────────────────────────────────────────────────

def test_polish_cleans_a_realistic_instagram_caption():
    raw = ("Meal prep doesn't have to be overwhelming.\n\n"
           "1. **Cook grains in bulk**: rice, quinoa, or pasta—pick your favorite.\n"
           "2. **Prep proteins**: bake chicken or marinate tofu.\n\n"
           "[grabguides.com](https://grabguides.com/blog/2-hour-sunday-meal-prep-system)")
    out = polish(raw, keep_urls=False)
    assert "**" not in out
    assert "](" not in out and "http" not in out
    assert "—" not in out
    assert out.endswith("bake chicken or marinate tofu.")
    assert "1. Cook grains in bulk: rice, quinoa, or pasta, pick your favorite." in out
