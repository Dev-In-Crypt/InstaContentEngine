from services.trend_extractor import (
    compute_engagement_score, extract_cta, extract_hashtags, extract_hook,
)


def test_extract_hashtags_dedup_case_insensitive():
    cap = "Run hard #Running #fitness #Running #Marathon"
    tags = extract_hashtags(cap)
    assert tags == ["#Running", "#fitness", "#Marathon"]


def test_extract_hashtags_empty():
    assert extract_hashtags("") == []
    assert extract_hashtags(None) == []


def test_extract_hook_strips_leading_emoji_and_trailing_hashtags():
    cap = "🔥 Wake up earlier and own your day. #morning #habits"
    assert extract_hook(cap) == "Wake up earlier and own your day."


def test_extract_hook_caps_at_150_chars():
    long = "x" * 300
    out = extract_hook(long)
    assert out is not None and len(out) <= 151  # 150 + ellipsis


def test_extract_hook_returns_none_for_only_hashtags():
    assert extract_hook("#one #two") is None


def test_extract_cta_imperative():
    cap = "Some body text.\n\nFollow for more running tips!"
    assert extract_cta(cap) == "Follow for more running tips!"


def test_extract_cta_link_in_bio():
    cap = "Story here.\n\nGrab the plan — link in bio."
    assert extract_cta(cap) is not None
    assert "link in bio" in extract_cta(cap).lower()


def test_extract_cta_returns_none_when_no_cta():
    cap = "Just a thought I had today, nothing more."
    assert extract_cta(cap) is None


def test_compute_engagement_score_views_dominant():
    assert compute_engagement_score(10, 5, 1000) > compute_engagement_score(1000, 1000, None)


def test_compute_engagement_score_no_views():
    # Comments weighted higher than likes
    assert compute_engagement_score(0, 10, None) > compute_engagement_score(10, 0, None)


def test_compute_engagement_score_zero():
    assert compute_engagement_score(0, 0, None) == 0.0
