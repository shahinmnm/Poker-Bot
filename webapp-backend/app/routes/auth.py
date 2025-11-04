from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Optional, Dict, Any
import secrets
import os
import json
from urllib.parse import parse_qsl

from app.utils import verify_telegram_init_data

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Get bot token from environment (support both legacy and new names)
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")


class TelegramAuthRequest(BaseModel):
    """Request body for Telegram WebApp authentication"""

    initData: str
    user: Optional[Dict[str, Any]] = None


class AuthSuccessResponse(BaseModel):
    """Successful authentication response"""

    success: bool = True
    token: str
    user: Dict[str, Any]


@router.post("/telegram", response_model=AuthSuccessResponse)
async def authenticate_telegram_user(data: TelegramAuthRequest):
    """Verify Telegram WebApp initData and issue a session token."""

    # Verify the signature
    if not verify_telegram_init_data(data.initData, BOT_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram signature",
        )

    # Parse user data from initData or use provided user object
    parsed = dict(parse_qsl(data.initData))
    user_json = parsed.get("user")

    if user_json:
        try:
            user_data = json.loads(user_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid user JSON payload",
            ) from exc
    elif data.user:
        user_data = data.user
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing user data",
        )

    # Generate a secure session token
    session_token = secrets.token_urlsafe(32)

    # TODO: Store session in Redis/database with expiry
    # await redis.setex(f"session:{session_token}", 3600, json.dumps(user_data))

    return AuthSuccessResponse(token=session_token, user=user_data)


@router.get("/verify")
async def verify_session(token: str):
    """Verify if a session token is valid."""

    # TODO: Check token in Redis/database
    # user_data = await redis.get(f"session:{token}")
    # if not user_data:
    #     raise HTTPException(401, "Invalid or expired session")

    return {"valid": True, "message": "Token verification not yet implemented"}
