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
    NICHE_BOX_PALETTE, BrandVoiceResponse, BrandVoiceUpdate, ProfileResponse, ProfileUpdate,
    SlideStyleResponse, SlideStyleUpdate,
)
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
