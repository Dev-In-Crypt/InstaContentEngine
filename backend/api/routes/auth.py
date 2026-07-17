"""Registration, login, and 'who am I' for the multi-tenant SaaS.

In local (desktop) mode these are unused — get_current_user returns the implicit
local owner without a token. In cloud mode the frontend registers/logs in and
stores the returned JWT.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from models.database import User as UserModel
from services.auth import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)


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


@router.post("/register", response_model=TokenResponse)
async def register(
    body: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    email = body.email.lower()
    existing = (await db.execute(
        select(UserModel).where(UserModel.email == email)
    )).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = UserModel(email=email, password_hash=hash_password(body.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    user = (await db.execute(
        select(UserModel).where(UserModel.email == body.email.lower())
    )).scalars().first()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=MeResponse)
async def me(
    user: Annotated[UserModel, Depends(get_current_user)],
) -> MeResponse:
    return MeResponse(
        id=user.id, email=user.email,
        is_local=bool(user.is_local), is_admin=bool(user.is_admin),
    )
