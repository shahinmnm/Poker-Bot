#!/usr/bin/env python3
import hashlib
import hmac
import os
import urllib.parse
import uuid
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.redis_client import get_redis_client
from app.routes import game, health
from app.websocket_manager import websocket_endpoint

app = FastAPI(title="Poker WebApp API", version="1.0.0")


class TelegramAuthData(BaseModel):
    initData: str
    user: Dict[str, Any]


def verify_telegram_webapp_data(init_data: str, bot_token: str) -> bool:
    """Verify that the request originates from Telegram."""
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        received_hash = parsed_data.pop("hash", None)

        if not received_hash:
            return False

        data_check_arr = [f"{k}={v}" for k, v in sorted(parsed_data.items())]
        data_check_string = "\n".join(data_check_arr)

        secret_key = hmac.new(
            "WebAppData".encode(),
            bot_token.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(calculated_hash, received_hash)
    except Exception:
        return False


def create_session(user_id: Any) -> str:
    """Create a user session stored in Redis with a configurable TTL."""
    token = uuid.uuid4().hex
    session_key = f"session:{token}"
    ttl = int(os.getenv("SESSION_TTL", 86400))

    redis = get_redis_client()
    redis.setex(session_key, ttl, user_id)

    return token

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("CORS_ORIGINS", "*")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(health.router)
app.include_router(game.router, prefix="/game", tags=["game"])


@app.post("/api/auth/telegram")
async def authenticate_telegram_user(data: TelegramAuthData):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not bot_token:
        raise HTTPException(status_code=500, detail="Bot token is not configured")

    if not verify_telegram_webapp_data(data.initData, bot_token):
        raise HTTPException(status_code=401, detail="Invalid Telegram data")

    user_id = data.user.get("id")
    if user_id is None:
        raise HTTPException(status_code=400, detail="Missing user identifier")

    session_token = create_session(user_id)

    return {
        "success": True,
        "token": session_token,
        "user": data.user,
    }

# WebSocket
app.add_websocket_route("/ws", websocket_endpoint)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
