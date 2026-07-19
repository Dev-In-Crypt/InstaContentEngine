"""Brand voice presets — the editable style/personality half of the generation
prompt. A preset's `text` fills the `{brand_voice}` slot in the caption prompts
(caption_generator), which sits BEFORE the RULES + JSON-format contract, so a
voice only steers style/tone and can't change the required output shape.

Presets are voice ARCHETYPES (how an account sounds), not niches/topics/formats —
those are separate controls (niche, target_audience, tone, length). One archetype
works across any niche; a user running several accounts picks the matching preset
per post.
"""
from __future__ import annotations

DEFAULT_PRESET = "balanced"
CUSTOM = "custom"
MAX_CUSTOM_LEN = 800

# key -> {label, description (for UI), text (fills {brand_voice})}
BRAND_VOICE_PRESETS: dict[str, dict[str, str]] = {
    "balanced": {
        "label": "Balanced",
        "description": "Versatile, clear, and friendly-professional. A safe default for any account.",
        "text": ("A versatile, modern brand voice: clear, friendly, and credible. "
                 "Approachable but never sloppy — informative without being stiff."),
    },
    "professional": {
        "label": "Professional & Expert",
        "description": "Authoritative and credible. For B2B, consultants, and experts.",
        "text": ("An authoritative, expert voice. Confident and credible, backed by substance. "
                 "Precise and polished; no hype and no filler — it earns trust through clarity."),
    },
    "friendly": {
        "label": "Friendly & Conversational",
        "description": "Warm and relatable, like a helpful friend. For lifestyle and personal brands.",
        "text": ("A warm, conversational voice, like talking to a helpful friend. First person, "
                 "relatable, and encouraging. Casual and human, with genuine enthusiasm."),
    },
    "bold": {
        "label": "Bold & Motivational",
        "description": "High-energy and punchy. For fitness, coaching, and motivation.",
        "text": ("A bold, high-energy, motivational voice. Punchy short lines and a strong push to "
                 "act. Confident and empowering — it fires people up to take action."),
    },
    "educational": {
        "label": "Educational & Clear",
        "description": "Teaches step by step in plain language. For how-to, tech, and explainers.",
        "text": ("A clear, patient teacher's voice. Explains ideas step by step in plain language "
                 "and makes complex things simple. Practical and example-led."),
    },
    "storyteller": {
        "label": "Storyteller",
        "description": "Narrative and emotional. For creators, bloggers, and personal stories.",
        "text": ("A narrative, story-first voice. Opens on a vivid moment or tension, builds a small "
                 "emotional arc, and lands a takeaway. Personal, evocative, and human."),
    },
    "playful": {
        "label": "Playful & Witty",
        "description": "Light humor and wordplay. For food, entertainment, and youthful brands.",
        "text": ("A playful, witty voice with light humor and clever wordplay. Fun and upbeat but "
                 "still useful, and it never tries too hard."),
    },
    "luxury": {
        "label": "Luxury & Aspirational",
        "description": "Elegant and refined. For premium, beauty, and travel brands.",
        "text": ("An elegant, aspirational voice. Refined, calm, and sensory, evoking quality and "
                 "craft. Understated confidence — sophisticated, never loud or salesy."),
    },
    "minimal": {
        "label": "Minimal & Direct",
        "description": "Short, concrete, benefit-led. For DTC and product accounts.",
        "text": ("A minimal, direct voice. Short, concrete sentences focused on the benefit. "
                 "No fluff and no filler — clarity and precision above all."),
    },
}


def list_presets() -> list[dict[str, str]]:
    """Preset keys + labels + descriptions for the settings UI (no prompt text),
    with the synthetic 'custom' option appended."""
    out = [{"key": k, "label": v["label"], "description": v["description"]}
           for k, v in BRAND_VOICE_PRESETS.items()]
    out.append({"key": CUSTOM, "label": "Custom",
                "description": "Write your own brand voice in your words."})
    return out


def is_valid_preset(preset: str | None) -> bool:
    return preset in BRAND_VOICE_PRESETS or preset == CUSTOM


def resolve_brand_voice(preset: str | None, custom: str | None = None) -> str:
    """Return the voice text to inject into the prompt.

    - 'custom' + non-empty custom text → that text (trimmed/capped)
    - a known preset key → its text
    - None / unknown / empty custom → the default preset's text
    """
    if preset == CUSTOM:
        cleaned = (custom or "").strip()[:MAX_CUSTOM_LEN]
        if cleaned:
            return cleaned
        return BRAND_VOICE_PRESETS[DEFAULT_PRESET]["text"]
    if preset in BRAND_VOICE_PRESETS:
        return BRAND_VOICE_PRESETS[preset]["text"]
    return BRAND_VOICE_PRESETS[DEFAULT_PRESET]["text"]
