# webapp-backend/app/main.py
from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, APIRouter, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger("app.main")
logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))

# -----------------------------------------------------------------------------
# App & CORS
# -----------------------------------------------------------------------------
def _split_csv(env_val: Optional[str]) -> List[str]:
    if not env_val:
        return []
    return [x.strip() for x in env_val.split(",") if x.strip()]

CORS_ENV = (
    os.environ.get("CORS_ORIGINS")
    or os.environ.get("POKER_CORS_ORIGINS")
    or os.environ.get("POKERBOT_CORS_ORIGINS")
    or os.environ.get("WEBAPP_CORS_ORIGINS")
    or "https://poker.shahin8n.sbs"
)
ALLOWED = _split_csv(CORS_ENV) or ["*"]

app = FastAPI(title="Poker WebApp API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info("🚀 Poker WebApp API starting...")
logger.info("📍 CORS origins: %s", ALLOWED)

# -----------------------------------------------------------------------------
# Health (used by Nginx /health check)
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}

# -----------------------------------------------------------------------------
# Mini-App endpoints (INLINE, no external app.routers import needed)
# These are mounted twice (with and without /api) to work with your Nginx rewrite.
# -----------------------------------------------------------------------------
router = APIRouter()

def _get_user_id_from_request(request: Request, user_id: Optional[int]) -> int:
    """
    Telegram WebApp normally passes initData via 'X-Telegram-Init-Data'.
    For dev/curl, allow ?user_id=... when POKER_DEV_ALLOW_FALLBACK=1.
    """
    if user_id is not None:
        return int(user_id)

    # If you want to parse Telegram init data in production, do it here.
    # For now, rely on fallback when allowed.
    if os.environ.get("POKER_DEV_ALLOW_FALLBACK") == "1":
        return 1  # dev user

    raise HTTPException(status_code=401, detail="Missing user identity")

@router.get("/user/settings")
async def get_user_settings(request: Request, user_id: Optional[int] = None):
    uid = _get_user_id_from_request(request, user_id)
    # Minimal settings payload the frontend expects
    return {
        "user_id": uid,
        "theme": "auto",               # auto | dark | light
        "notifications": True,
        "locale": "en",
        "currency": "chips",
        "experimental": False,
    }

@router.get("/user/stats")
async def get_user_stats(request: Request, user_id: Optional[int] = None):
    uid = _get_user_id_from_request(request, user_id)
    # Simple demo stats; you can wire real values later
    return {
        "user_id": uid,
        "hands_played": 124,
        "biggest_win": 15200,
        "biggest_loss": -4800,
        "win_rate": 0.56,
        "last_played": (datetime.utcnow() - timedelta(hours=6)).isoformat() + "Z",
        "streak_days": 3,
        "chip_balance": 25000,
        "rank": "Rising Shark",
    }

@router.get("/tables")
async def list_tables():
    # Minimal lobby list; extend with real engine/redis later
    return {
        "tables": [
            {
                "id": "pub-1",
                "name": "Main Lobby",
                "stakes": "50/100",
                "players_count": 5,
                "max_players": 9,
                "is_private": False,
                "status": "waiting",  # waiting | running
            },
            {
                "id": "pub-2",
                "name": "Turbo Sit&Go",
                "stakes": "100/200",
                "players_count": 9,
                "max_players": 9,
                "is_private": False,
                "status": "running",
            },
            {
                "id": "grp-777",
                "name": "Friends Table",
                "stakes": "10/20",
                "players_count": 3,
                "max_players": 6,
                "is_private": True,
                "status": "waiting",
            },
        ]
    }

# Mount both ways to match your Nginx (which strips /api) and also support /api/*
app.include_router(router)                 # /user/*, /tables
app.include_router(router, prefix="/api")  # /api/user/*, /api/tables

# -----------------------------------------------------------------------------
# Startup: print routes for quick diagnostics
# -----------------------------------------------------------------------------
@app.on_event("startup")
async def _on_startup() -> None:
    logger.info("📡 Routes registered:")
    for r in app.routes:
        try:
            methods = getattr(r, "methods", {"GET"})
            path = getattr(r, "path")
            if path:
                logger.info("  %s %s", methods, path)
        except Exception:
            continue

# -----------------------------------------------------------------------------
# Local dev runner (container uses `uvicorn app.main:app`)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=True)
