from services.pillars import (
    MLMG_PILLARS, classify_pillar, pillar_mix, suggest_today, DEFAULT_PILLAR,
)


def test_classify_educational():
    assert classify_pillar("How to run your first 5K", "Tips and a step by step guide") == "educational"


def test_classify_inspirational():
    assert classify_pillar("Discipline beats motivation", "mindset and consistency") == "inspirational"


def test_classify_community():
    assert classify_pillar("What is your biggest challenge?", "comment below and tag a friend") == "community"


def test_classify_fallback():
    assert classify_pillar("", "") == DEFAULT_PILLAR


def test_pillar_mix_shapes():
    mix = pillar_mix(["educational", "educational", "inspirational", None])
    by = {m["key"]: m for m in mix}
    assert len(mix) == len(MLMG_PILLARS)
    # 2 of 4 educational (None falls back to educational) = 3/4 → 75%
    assert by["educational"]["count"] == 3
    assert by["educational"]["actual_pct"] == 75.0
    assert by["inspirational"]["count"] == 1


def test_suggest_today_picks_biggest_gap():
    # All posts educational → inspirational (25% target, 0 actual) should be suggested
    mix = pillar_mix(["educational"] * 5)
    sug = suggest_today(mix)
    # biggest gap is educational is over-served; inspirational most under
    assert sug["pillar"] in {"inspirational", "personal", "community", "product"}
    assert "below" in sug["reason"].lower()


def test_suggest_today_empty():
    sug = suggest_today(pillar_mix([]))
    assert sug["pillar"] in {m["key"] for m in MLMG_PILLARS}
