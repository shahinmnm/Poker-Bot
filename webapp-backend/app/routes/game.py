from fastapi import APIRouter, HTTPException
from app.redis_client import get_redis_client

router = APIRouter()

@router.get("/status/{game_id}")
async def get_game_status(game_id: str):
    """Get game status from Redis."""
    redis_client = get_redis_client()
    
    # بررسی وجود بازی
    game_key = f"game:{game_id}"
    if not redis_client.exists(game_key):
        raise HTTPException(status_code=404, detail="Game not found")
    
    # دریافت اطلاعات بازی
    game_data = redis_client.hgetall(game_key)
    
    return {
        "game_id": game_id,
        "data": game_data
    }
