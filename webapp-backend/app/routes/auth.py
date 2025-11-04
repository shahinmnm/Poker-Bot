"""Authentication routes for Telegram WebApp logins."""

import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.utils import (
    generate_session_token,
    verify_session_token,
    verify_telegram_init_data,
)

# Routes live directly under /auth so that the backend aligns with the
# Telegram WebApp's expected endpoints (frontend already calls /auth/*).
router = APIRouter(prefix="/auth", tags=["auth"])

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")


class TelegramAuthPayload(BaseModel):
    """Request body for Telegram WebApp authentication."""

    init_data: str = Field(..., alias="initData")

    model_config = {"populate_by_name": True}


class TelegramAuthResponse(BaseModel):
    """Successful authentication response."""

    success: bool = True
    session_token: str
    user: Dict[str, Any]
    expires_in: int


@router.post("/telegram", response_model=TelegramAuthResponse)
async def telegram_login(payload: TelegramAuthPayload):
    """Verify Telegram init data and issue a session token."""

    user_data = verify_telegram_init_data(payload.init_data, BOT_TOKEN)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram signature",
        )

    user_id = user_data.get("id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing user identifier",
        )

    session_token = generate_session_token(
        user_id=user_id,
        username=user_data.get("username"),
        ttl_seconds=86_400,
    )

    return TelegramAuthResponse(
        session_token=session_token,
        user=user_data,
        expires_in=86_400,
    )


@router.get("/verify")
async def verify_session(token: str):
    """Verify if a session token is valid."""

    session = verify_session_token(token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    return {"valid": True, "user": session}
