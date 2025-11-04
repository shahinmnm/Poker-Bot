from fastapi import APIRouter, Response, Depends
from app.dependencies import get_current_user, get_or_create_session, get_redis_client
from app.models import User

router = APIRouter(tags=["auth"])


@router.post("/login")
async def login(
    response: Response,
    redis = Depends(get_redis_client)
):
    """Initialize session and set cookie"""
    session_id, user = await get_or_create_session(response, redis)

    return {
        "status": "authenticated",
        "session_id": session_id,
        "user_id": user.id
    }


@router.post("/auth/login")
async def login_legacy(
    response: Response,
    redis = Depends(get_redis_client)
):
    """Legacy login path for routers without a global prefix."""
    return await login(response, redis)


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    """Get current user info"""
    return {
        "id": user.id,
        "username": user.username,
        "telegram_id": user.telegram_id
    }


@router.get("/auth/me")
async def get_me_legacy(user: User = Depends(get_current_user)):
    """Legacy user info path for routers without a global prefix."""
    return await get_me(user)
