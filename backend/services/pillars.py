"""Content pillars for My Life My Game — a fixed 5-pillar mix with target
shares, keyword-based classification, and a 'what to post today' suggestion.

Pure functions, no DB / LLM. The API layer feeds in Post rows.
"""

from __future__ import annotations

from typing import Optional


MLMG_PILLARS: list[dict] = [
    {"key": "educational", "label": "Educational", "target_pct": 35, "emoji": "📚",
     "keywords": ["how to", "how-to", "tips", "guide", "learn", "mistake", "science",
                  "why", "steps", "beginner", "explained", "avoid"]},
    {"key": "inspirational", "label": "Inspirational", "target_pct": 25, "emoji": "🔥",
     "keywords": ["motivation", "discipline", "mindset", "goal", "overcome", "believe",
                  "push", "consistency", "never give up", "stronger"]},
    {"key": "personal", "label": "Behind-the-scenes", "target_pct": 15, "emoji": "🎬",
     "keywords": ["my", "today", "routine", "story", "journey", "morning", "behind the",
                  "day in", "i tried", "week"]},
    {"key": "community", "label": "Community", "target_pct": 15, "emoji": "🤝",
     "keywords": ["you", "comment", "share", "challenge", "question", "tag a", "let's",
                  "your turn", "who else", "join"]},
    {"key": "product", "label": "Product / Value", "target_pct": 10, "emoji": "⭐",
     "keywords": ["plan", "program", "link in bio", "download", "offer", "free guide",
                  "sign up", "coaching", "ebook", "checklist"]},
]

_PILLAR_BY_KEY = {p["key"]: p for p in MLMG_PILLARS}
DEFAULT_PILLAR = "educational"


def classify_pillar(topic: Optional[str], caption: Optional[str] = "") -> str:
    """Keyword-score classification. Returns a pillar key; falls back to
    'educational' when nothing matches."""
    text = f"{topic or ''} {caption or ''}".lower()
    best_key, best_score = DEFAULT_PILLAR, 0
    for p in MLMG_PILLARS:
        score = sum(1 for kw in p["keywords"] if kw in text)
        if score > best_score:
            best_key, best_score = p["key"], score
    return best_key


def pillar_mix(pillars_of_posts: list[Optional[str]]) -> list[dict]:
    """Given a list of pillar keys (one per post), return the actual vs target
    mix per pillar. `None` is treated as the default pillar."""
    counts: dict[str, int] = {p["key"]: 0 for p in MLMG_PILLARS}
    for key in pillars_of_posts:
        k = key if key in counts else (DEFAULT_PILLAR if key is None else key)
        if k in counts:
            counts[k] += 1
    total = sum(counts.values())   # keep total consistent with what we counted
    out = []
    for p in MLMG_PILLARS:
        c = counts.get(p["key"], 0)
        actual = round(100 * c / total, 1) if total else 0.0
        out.append({
            "key": p["key"], "label": p["label"], "emoji": p["emoji"],
            "target_pct": p["target_pct"], "actual_pct": actual, "count": c,
        })
    return out


def suggest_today(mix: list[dict]) -> dict:
    """Pick the most under-served pillar (largest target − actual gap)."""
    if not mix:
        p = _PILLAR_BY_KEY[DEFAULT_PILLAR]
        return {"pillar": p["key"], "label": p["label"], "emoji": p["emoji"],
                "reason": "Start with an educational post."}
    worst = max(mix, key=lambda m: m["target_pct"] - m["actual_pct"])
    gap = round(worst["target_pct"] - worst["actual_pct"], 1)
    if gap <= 0:
        return {"pillar": worst["key"], "label": worst["label"], "emoji": worst["emoji"],
                "reason": "Your mix looks balanced — keep it up."}
    return {
        "pillar": worst["key"], "label": worst["label"], "emoji": worst["emoji"],
        "reason": f"You're {gap:.0f}% below your {worst['label']} target — post one today.",
    }
