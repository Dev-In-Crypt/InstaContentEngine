from fastapi import APIRouter, Depends, Query, HTTPException
from api.deps import get_stock, require_token
from models.schemas import StockPhoto
from services.stock import StockClient, StockError

router = APIRouter(prefix="/api/stock", tags=["stock"])


@router.get("/search", response_model=list[StockPhoto], dependencies=[Depends(require_token)])
async def search_stock(
    query: str = Query(..., min_length=2),
    source: str = Query("unsplash", pattern="^(unsplash|pexels)$"),
    per_page: int = Query(5, ge=1, le=20),
    stock: StockClient = Depends(get_stock),
) -> list[StockPhoto]:
    try:
        results = await stock.search(query=query, per_page=per_page, source=source)
    except StockError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return [
        StockPhoto(
            id=r.id,
            url=r.url,
            thumb_url=r.thumb_url,
            alt=r.alt,
            source=r.source,
        )
        for r in results
    ]
