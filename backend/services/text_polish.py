"""Making model output publishable, deterministically.

The prompts already ask for plain text, no markdown, no em dashes — and models
ignore all three often enough that it shows up in every live run. No social
network renders markdown, so "**Cook grains**" and "[JAMA study](https://…)" go
out with the syntax visible; a trailing block of source links is dead weight in
a caption; and an em dash is the tell that a post was written by a model.

So the rules live here as pure functions instead of hopeful prompt lines. Length
enforcement (X) runs AFTER this, since polishing changes the length.
"""
from __future__ import annotations

import re

#: [label](https://…) — the shape a web-grounded model reaches for by default.
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
#: **bold** / __bold__ / *italic* / _italic_ / `code`, paired around real text.
#: The lookarounds keep it off word-internal markers — "file_name.py" and "snake_case"
#: are not emphasis, and "3 * 4" is arithmetic.
_MD_EMPHASIS = re.compile(r"(?<![\w*_`])(\*\*|__|\*|_|`)(\S(?:[^\n]*?\S)?)\1(?![\w*_`])")
#: "## Heading" at the start of a line.
_MD_HEADING = re.compile(r"^#{1,6}[ \t]+", re.MULTILINE)

#: An em dash, or an en dash used as punctuation. "6–12 months" is a range, not
#: punctuation, so a dash with digits on both sides is left alone.
_PUNCT_DASH = re.compile(r"\s*—\s*|(?<!\d)\s*–\s*|\s*–\s*(?!\d)")

_URL = re.compile(r"https?://\S+")
#: A bare domain the model drops in instead of a link: "example.com", "sub.example.co.uk".
_BARE_DOMAIN = re.compile(r"\b(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?\b", re.IGNORECASE)


def strip_markdown(text: str, keep_urls: bool = True) -> str:
    """Flatten markdown that the network would publish literally.

    `keep_urls` decides what a link becomes: "label (https://…)" where the URL is
    clickable and worth the characters (X, LinkedIn), or just "label" where it is
    dead text (Instagram). Emphasis and headings lose only their markers. List
    dashes stay — they read fine everywhere.
    """
    text = text or ""
    text = _MD_LINK.sub(r"\1 (\2)" if keep_urls else r"\1", text)
    text = _MD_HEADING.sub("", text)
    # Twice: "**_word_**" needs an inner pass. Bounded, so no runaway loop.
    for _ in range(2):
        text = _MD_EMPHASIS.sub(r"\2", text)
    return text


def flatten_dashes(text: str) -> str:
    """Replace dash-as-punctuation with a comma.

    "your body can keep going—your brain says stop" reads the same with a comma
    and stops looking machine-written. Numeric ranges ("6–12 months") keep their
    dash: that is not punctuation, it is a value.
    """
    return _PUNCT_DASH.sub(", ", text or "")


def drop_link_only_lines(text: str) -> str:
    """Remove trailing lines that are nothing but links.

    Grounded models like to append a source list to the caption, even when told
    not to — the sources already have their own panel in the UI. Only the TAIL is
    trimmed: a link inside a sentence is part of the text and stays.

    A line whose whole content is a URL or bare domain counts as a link line, so
    a final "Read more at example.com" is dropped too. That is a caption the user
    can still type back in step 4, and on Instagram the link would not have been
    clickable anyway.
    """
    lines = (text or "").split("\n")
    while lines:
        stripped = lines[-1].strip()
        if not stripped:
            lines.pop()
            continue
        residue = _BARE_DOMAIN.sub("", _URL.sub("", stripped))
        # Nothing but punctuation/whitespace left once the links are removed.
        if residue.strip(" \t.,;:!?-–—()[]{}<>\"'|•*"):
            break
        lines.pop()
    return "\n".join(lines).rstrip()


def polish(text: str, keep_urls: bool = True) -> str:
    """The single entry point: markdown → trailing link dump → dashes."""
    return flatten_dashes(drop_link_only_lines(strip_markdown(text, keep_urls))).strip()
