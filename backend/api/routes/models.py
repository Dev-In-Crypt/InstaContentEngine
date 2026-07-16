from typing import Annotated
from fastapi import APIRouter, Depends
from models.schemas import ModelInfo
from services.openrouter import TEXT_MODELS, IMAGE_MODELS
from api.deps import get_settings, require_token
from config import Settings

router = APIRouter(prefix="/api/models", tags=["models"], dependencies=[Depends(require_token)])


@router.get("/text", response_model=list[ModelInfo])
async def list_text_models() -> list[ModelInfo]:
    return [
        ModelInfo(id=model_id, name=alias, provider=model_id.split("/")[0])
        for alias, model_id in TEXT_MODELS.items()
    ]


@router.get("/image", response_model=list[ModelInfo])
async def list_image_models() -> list[ModelInfo]:
    return [
        ModelInfo(id=model_id, name=alias, provider=model_id.split("/")[0])
        for alias, model_id in IMAGE_MODELS.items()
    ]


@router.get("/defaults")
async def get_default_models(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Returns the default models configured in .env."""
    return {
        "text_model": settings.default_text_model,
        "image_model": settings.default_image_model,
    }
