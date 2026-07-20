"""Provider-agnostic AI interfaces.

Each tenant picks a provider + model + their own API key, separately for text and
for images. Everything above this layer (caption_generator, image_router) talks to
these protocols and never to a concrete vendor client.

The text signature deliberately matches what `OpenRouterClient.generate_text`
already returned — `(content, citations)` — so the generators barely change.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


class AIError(Exception):
    """Any provider failure, normalised. The message is user-facing: it is shown
    in the SSE error event and in the settings "Test" button."""


@runtime_checkable
class TextProvider(Protocol):
    """Generates post copy."""

    #: Only OpenRouter performs live web search (Exa.ai via the ":online" suffix).
    #: When False the caller still works, it just gets no citations.
    supports_grounding: bool

    async def generate_text(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2000,
        web_grounded: bool = False,
    ) -> tuple[str, list[dict]]:
        """Return (content, citations) where citations are [{title, url}]."""
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class ImageProvider(Protocol):
    """Generates slide background images."""

    async def generate_image(self, model: str, prompt: str) -> bytes:
        """Return raw image bytes (JPEG/PNG)."""
        ...

    async def close(self) -> None:
        ...


def require_model(model: Optional[str], kind: str) -> str:
    """Guard used by every adapter: a model must be chosen explicitly. There is no
    platform default in cloud mode — the user picks one in Account → AI models."""
    if not (model or "").strip():
        raise AIError(
            f"No {kind} model selected. Choose one in Account → AI models."
        )
    return model.strip()
