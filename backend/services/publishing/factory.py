"""Build the right Publisher for a post's platform (and gate its credentials)."""
from __future__ import annotations

from services.publishing.base import Publisher, PublisherError
from services.publishing.instagram_adapter import InstagramPlatformPublisher
from services.publishing.x import XPublisher


def make_publisher_for(platform: str, settings, name_prefix: str = "slide") -> Publisher:
    if platform == "instagram":
        if not (settings.instagram_access_token and settings.instagram_user_id):
            raise PublisherError("Instagram credentials not configured")
        if not settings.imgbb_api_key:
            raise PublisherError("IMGBB_API_KEY not configured (needed for public image URLs)")
        return InstagramPlatformPublisher(
            settings.instagram_access_token, settings.instagram_user_id,
            settings.imgbb_api_key, name_prefix,
        )
    if platform == "x":
        if not all((settings.x_api_key, settings.x_api_secret,
                    settings.x_access_token, settings.x_access_token_secret)):
            raise PublisherError("X (Twitter) API credentials not configured")
        return XPublisher(
            settings.x_api_key, settings.x_api_secret,
            settings.x_access_token, settings.x_access_token_secret,
        )
    raise PublisherError(f"No publisher configured for platform: {platform!r}")
