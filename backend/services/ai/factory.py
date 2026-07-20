"""Build a provider from a (provider, key) pair.

Mirrors services/publishing/factory.py. Adapters are imported lazily so adding a
vendor never widens the import graph for everyone else.
"""
from __future__ import annotations

from services.ai.base import AIError
from services.ai.catalog import PROVIDERS, key_field_for  # noqa: F401  (re-export)


def _build(provider: str, api_key: str, ssl_verify: bool, referer: str, app_title: str):
    if provider == "openrouter":
        from services.ai.openrouter_provider import OpenRouterProvider
        return OpenRouterProvider(api_key=api_key, referer=referer,
                                  app_title=app_title, ssl_verify=ssl_verify)
    if provider == "openai":
        from services.ai.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key, ssl_verify=ssl_verify)
    if provider == "anthropic":
        from services.ai.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key, ssl_verify=ssl_verify)
    if provider == "google":
        from services.ai.google_provider import GoogleProvider
        return GoogleProvider(api_key=api_key, ssl_verify=ssl_verify)
    raise AIError(f"Unknown AI provider: {provider!r}")


def make_text_provider(provider: str, api_key: str, ssl_verify: bool = True,
                       referer: str = "https://localhost",
                       app_title: str = "InstaContentEngine"):
    if provider not in PROVIDERS:
        raise AIError(f"Unknown AI provider: {provider!r}")
    return _build(provider, api_key, ssl_verify, referer, app_title)


def make_image_provider(provider: str, api_key: str, ssl_verify: bool = True,
                        referer: str = "https://localhost",
                        app_title: str = "InstaContentEngine"):
    meta = PROVIDERS.get(provider)
    if meta is None:
        raise AIError(f"Unknown AI provider: {provider!r}")
    if not meta["image_models"]:
        raise AIError(f"{meta['label']} does not generate images — pick another provider.")
    return _build(provider, api_key, ssl_verify, referer, app_title)
