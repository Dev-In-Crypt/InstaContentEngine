"""The personal cabinet: a user stores their own API keys here (cloud mode).

Keys are encrypted before storage (services/secrets.encrypt) and NEVER returned
in plaintext — GET reports only which keys are set, plus a short masked tail.
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import _CRED_FIELDS, get_current_user, get_db
from models.database import User as UserModel, UserCredentials as UserCredentialsModel
from models.schemas import (
    NICHE_BOX_PALETTE, AISettingsResponse, AISettingsUpdate, AITestRequest, AITestResponse,
    BrandVoiceResponse, BrandVoiceUpdate, ProfileResponse, ProfileUpdate,
    SlideStyleResponse, SlideStyleUpdate,
)
from services.ai.catalog import IMAGE, PROVIDERS, TEXT, is_valid_provider
from services.brand_engine import BrandConfig
from services.brand_voice import DEFAULT_PRESET, is_valid_preset, list_presets
from services.secrets import decrypt, encrypt

router = APIRouter(prefix="/api/settings", tags=["settings"])

# The plaintext field names a user may set (same keys as _CRED_FIELDS).
_FIELDS = list(_CRED_FIELDS.keys())


class CredentialsUpdate(BaseModel):
    """Every field optional. A present empty string clears that key; an omitted
    field leaves it unchanged."""
    openrouter_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    instagram_access_token: Optional[str] = None
    instagram_user_id: Optional[str] = None
    imgbb_api_key: Optional[str] = None
    x_api_key: Optional[str] = None
    x_api_secret: Optional[str] = None
    x_access_token: Optional[str] = None
    x_access_token_secret: Optional[str] = None
    unsplash_access_key: Optional[str] = None
    pexels_api_key: Optional[str] = None


def _mask(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    tail = value[-4:] if len(value) >= 4 else value
    return f"••••{tail}"


@router.get("/credentials")
async def get_credentials(
    user: Annotated[UserModel, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Report which keys are set (never the raw values)."""
    creds = await db.get(UserCredentialsModel, user.id)
    out: dict[str, dict] = {}
    for field, column in _CRED_FIELDS.items():
        raw = decrypt(getattr(creds, column) or "") if creds else ""
        out[field] = {"set": bool(raw), "masked": _mask(raw)}
    return out


@router.put("/credentials")
async def put_credentials(
    body: CredentialsUpdate,
    user: Annotated[UserModel, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    creds = await db.get(UserCredentialsModel, user.id)
    if creds is None:
        creds = UserCredentialsModel(user_id=user.id)
        db.add(creds)
    for field in _FIELDS:
        value = getattr(body, field)
        if value is None:              # omitted → leave unchanged
            continue
        setattr(creds, _CRED_FIELDS[field], encrypt(value))   # "" clears it
    await db.commit()
    return {"status": "ok"}


# ── Brand voice (generation style preference — NOT a secret, stored plain) ──

@router.get("/brand-voice", response_model=BrandVoiceResponse)
async def get_brand_voice(
    user: Annotated[UserModel, Depends(get_current_user)],
) -> BrandVoiceResponse:
    return BrandVoiceResponse(
        preset=user.brand_voice_preset or DEFAULT_PRESET,
        custom=user.brand_voice_custom or "",
        presets=list_presets(),
    )


@router.put("/brand-voice")
async def put_brand_voice(
    body: BrandVoiceUpdate,
    user: Annotated[UserModel, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    if body.preset is not None:
        if not is_valid_preset(body.preset):
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="Unknown brand voice preset")
        user.brand_voice_preset = body.preset
    if body.custom is not None:            # "" clears the custom text
        user.brand_voice_custom = body.custom.strip() or None
    # A named preset means the custom text no longer applies; keep it stored but it's
    # only used when preset == "custom".
    await db.commit()
    return {"status": "ok"}


# ── Brand profile (niche/audience/brand — generation defaults, NOT a secret) ──

@router.get("/profile", response_model=ProfileResponse)
async def get_profile(
    user: Annotated[UserModel, Depends(get_current_user)],
) -> ProfileResponse:
    return ProfileResponse(
        niche=user.niche or "",
        target_audience=user.target_audience or "",
        brand_name=user.brand_name or "",
    )


@router.put("/profile")
async def put_profile(
    body: ProfileUpdate,
    user: Annotated[UserModel, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    if body.niche is not None:                     # "" clears
        user.niche = body.niche.strip() or None
    if body.target_audience is not None:
        user.target_audience = body.target_audience.strip() or None
    if body.brand_name is not None:
        user.brand_name = body.brand_name.strip() or None
    await db.commit()
    return {"status": "ok"}


# ── Slide colours (per-tenant branding of generated slides) ──────────────────

@router.get("/slide-style", response_model=SlideStyleResponse)
async def get_slide_style(
    user: Annotated[UserModel, Depends(get_current_user)],
) -> SlideStyleResponse:
    return SlideStyleResponse(
        accent_color=user.slide_accent_color or "",
        text_box_color=user.slide_text_box_color or "",
        default_accent_color=BrandConfig().niche_box_color,
        palette=NICHE_BOX_PALETTE,
    )


@router.put("/slide-style")
async def put_slide_style(
    body: SlideStyleUpdate,
    user: Annotated[UserModel, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    if body.accent_color is not None:          # "" resets to the platform default
        user.slide_accent_color = body.accent_color or None
    if body.text_box_color is not None:
        user.slide_text_box_color = body.text_box_color or None
    await db.commit()
    return {"status": "ok"}


# ── AI provider + model (each tenant picks, and pays for, their own) ─────────

@router.get("/ai", response_model=AISettingsResponse)
async def get_ai_settings(
    user: Annotated[UserModel, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AISettingsResponse:
    creds = await db.get(UserCredentialsModel, user.id)
    keys: dict[str, dict] = {}
    for provider, meta in PROVIDERS.items():
        column = _CRED_FIELDS.get(meta["key_field"])
        raw = decrypt(getattr(creds, column) or "") if (creds and column) else ""
        keys[provider] = {"set": bool(raw), "masked": _mask(raw)}
    return AISettingsResponse(
        text_provider=user.text_provider or "",
        text_model=user.text_model or "",
        image_provider=user.image_provider or "",
        image_model=user.image_model or "",
        keys=keys,
    )


@router.put("/ai")
async def put_ai_settings(
    body: AISettingsUpdate,
    user: Annotated[UserModel, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from fastapi import HTTPException

    if body.text_provider is not None:
        if body.text_provider and not is_valid_provider(body.text_provider, TEXT):
            raise HTTPException(status_code=422, detail="Unknown text provider")
        user.text_provider = body.text_provider or None
    if body.image_provider is not None:
        if body.image_provider and not is_valid_provider(body.image_provider, IMAGE):
            raise HTTPException(
                status_code=422,
                detail="Unknown image provider (note: Anthropic cannot generate images)")
        user.image_provider = body.image_provider or None
    # Any model id is accepted — the catalogue is a shortlist, not a whitelist, so a
    # newly released model is usable without waiting for a release.
    if body.text_model is not None:
        user.text_model = body.text_model.strip() or None
    if body.image_model is not None:
        user.image_model = body.image_model.strip() or None
    await db.commit()
    return {"status": "ok"}


@router.post("/ai/test", response_model=AITestResponse)
async def test_ai_settings(
    body: AITestRequest,
    user: Annotated[UserModel, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AITestResponse:
    """Prove the provider + model + key actually work, before the user relies on
    them. This is what catches a retired model id up front instead of mid-generation."""
    from services.ai.base import AIError
    from services.ai.factory import make_image_provider, make_text_provider
    from services.user_settings import build_settings_for_user, resolve_ai_choice

    kind = "image" if body.kind == "image" else "text"
    settings = await build_settings_for_user(db, user)
    provider, model, api_key = resolve_ai_choice(user, settings, kind)
    if not provider or not model:
        return AITestResponse(ok=False, message=f"Choose a {kind} provider and model first.")
    if not api_key:
        label = PROVIDERS[provider]["label"] if provider in PROVIDERS else provider
        return AITestResponse(ok=False, message=f"Add your {label} API key first.")

    client = None
    try:
        if kind == "image":
            client = make_image_provider(provider, api_key, ssl_verify=settings.ssl_verify)
            await client.generate_image(model=model, prompt="A single small grey square.")
        else:
            client = make_text_provider(provider, api_key, ssl_verify=settings.ssl_verify)
            await client.generate_text(model=model, system_prompt="Reply with OK.",
                                       user_prompt="Say OK.", max_tokens=16)
        return AITestResponse(ok=True, message=f"{model} works.")
    except AIError as exc:
        return AITestResponse(ok=False, message=str(exc)[:400])
    except Exception as exc:                      # never leak a stack trace to the UI
        return AITestResponse(ok=False, message=f"Test failed: {type(exc).__name__}")
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
