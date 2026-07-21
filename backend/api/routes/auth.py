"""Registration, login, email verification, password reset.

Local (desktop) mode never uses these — get_current_user returns the implicit
local owner. Cloud mode: register/login → JWT; verify/reset via emailed tokens.
"""
from datetime import timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.ratelimit import limiter
from models.database import User as UserModel
from services.auth import (
    create_access_token, create_purpose_token, decode_purpose_token,
    hash_password, verify_password,
)
from services.email import send_reset_email, send_verify_email

router = APIRouter(prefix="/api/auth", tags=["auth"])

_VERIFY_TTL = timedelta(hours=24)
_RESET_TTL = timedelta(hours=1)


_ACCOUNT_TYPES = {"creator", "business"}


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)
    # Which product the sign-up came from (the landing tab). Unknown/garbage
    # values fall back to "creator" rather than 422 — a stray query-param must
    # never block registration.
    account_type: str = "creator"

    @field_validator("account_type", mode="before")
    @classmethod
    def _normalise_account_type(cls, v: object) -> str:
        s = str(v or "").strip().lower()
        return s if s in _ACCOUNT_TYPES else "creator"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: str
    email: str
    is_local: bool = False
    is_admin: bool = False
    email_verified: bool = False
    account_type: str = "creator"
    active_account_id: Optional[str] = None


class ForgotRequest(BaseModel):
    email: EmailStr


class ResetRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=8, max_length=200)


@router.post("/register", response_model=TokenResponse)
@limiter.limit("5/minute;30/hour")
async def register(
    request: Request,
    body: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    email = body.email.lower()
    existing = (await db.execute(
        select(UserModel).where(UserModel.email == email)
    )).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = UserModel(email=email, password_hash=hash_password(body.password),
                     account_type=body.account_type)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await send_verify_email(email, create_purpose_token(user.id, "verify", _VERIFY_TTL))
    return TokenResponse(access_token=create_access_token(user.id, user.token_version))


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute;50/hour")
async def login(
    request: Request,
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    user = (await db.execute(
        select(UserModel).where(UserModel.email == body.email.lower())
    )).scalars().first()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id, user.token_version))


@router.get("/verify")
async def verify_email(token: str, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    user_id = decode_purpose_token(token, "verify")
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired link")
    user = await db.get(UserModel, user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired link")
    user.email_verified = True
    await db.commit()
    return {"status": "verified"}


@router.post("/resend-verification")
@limiter.limit("3/minute;10/hour")
async def resend_verification(
    request: Request,
    user: Annotated[UserModel, Depends(get_current_user)],
) -> dict:
    if not user.email_verified:
        await send_verify_email(user.email, create_purpose_token(user.id, "verify", _VERIFY_TTL))
    return {"status": "ok"}


@router.post("/forgot")
@limiter.limit("3/minute;10/hour")
async def forgot_password(
    request: Request,
    body: ForgotRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    user = (await db.execute(
        select(UserModel).where(UserModel.email == body.email.lower())
    )).scalars().first()
    if user and user.password_hash:   # local user has no password → skip
        await send_reset_email(user.email, create_purpose_token(user.id, "reset", _RESET_TTL))
    # Always 200 — don't reveal whether an email is registered.
    return {"status": "ok"}


@router.post("/reset")
@limiter.limit("5/minute;20/hour")
async def reset_password(
    request: Request,
    body: ResetRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    user_id = decode_purpose_token(body.token, "reset")
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired link")
    user = await db.get(UserModel, user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired link")
    user.password_hash = hash_password(body.password)
    user.email_verified = True   # resetting via emailed link proves email ownership
    user.token_version = (user.token_version or 0) + 1   # revoke all existing sessions
    await db.commit()
    return {"status": "ok"}


@router.post("/logout-all")
async def logout_all(
    user: Annotated[UserModel, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Invalidate every existing session for this user (e.g. a lost/stolen token)
    by bumping token_version. The caller's current token stops working too."""
    user.token_version = (user.token_version or 0) + 1
    await db.commit()
    return {"status": "ok"}


@router.get("/me", response_model=MeResponse)
async def me(
    user: Annotated[UserModel, Depends(get_current_user)],
) -> MeResponse:
    return MeResponse(
        id=user.id, email=user.email,
        is_local=bool(user.is_local), is_admin=bool(user.is_admin),
        email_verified=bool(user.email_verified),
        account_type=user.account_type or "creator",
        active_account_id=getattr(user, "active_account_id", None),
    )
