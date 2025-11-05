# webapp-backend/app/main.py
from __future__ import annotations

import os
import logging
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, APIRouter, Depends, Header, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("app.main")
logging.basicConfig(level=logging.INFO)

# ---- CORS ----
cors_origins_env = os.getenv("CORS_ORIGINS", "")
CORS_ORIGINS: List[str] = (
    [o.strip() for o in cors_origins_env.split(",") if o.strip()] or ["*"]
)

app = FastAPI(title="Poker WebApp API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)

log.info("🚀 Poker WebApp API starting...")
log.info("📍 CORS origins: %s", CORS_ORIGINS)

# ---- Identity helper ----
class Identity(Dict[str, Any]):
    user_id: int

def get_identity(
    x_telegram_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data"),
    user_id: Optional[int] = Query(default=None),
) -> Identity:
    """
    Accept identity from Telegram header (no signature verification for dev),
    or from `?user_id=` as a local/dev fallback.
    """
    if x_telegram_init_data:
        # In production you'd verify the signature. For now we only need a stable id.
        return Identity(user_id=1)
    if user_id is not None:
        return Identity(user_id=int(user_id))
    raise HTTPException(status_code=401, detail="Missing user identity")

# ---- Mock data ----
MOCK_TABLES = [
    {"id": "pub-1", "name": "Main Lobby", "stakes": "50/100", "players_count": 5, "max_players": 9, "is_private": False, "status": "waiting"},
    {"id": "pub-2", "name": "Turbo Sit&Go", "stakes": "100/200", "players_count": 9, "max_players": 9, "is_private": False, "status": "running"},
    {"id": "grp-777", "name": "Friends Table", "stakes": "10/20", "players_count": 3, "max_players": 6, "is_private": True, "status": "waiting"},
]

# ---- Core router (mounted at / and /api) ----
router = APIRouter()

@router.get("/health")
def health() -> Dict[str, Any]:
    from datetime import datetime, timezone
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

@router.get("/tables")
def list_tables() -> Dict[str, Any]:
    return {"tables": MOCK_TABLES}

@router.post("/tables/{table_id}/join")
def join_table(table_id: str, ident: Identity = Depends(get_identity)) -> Dict[str, Any]:
    exists = any(t["id"] == table_id for t in MOCK_TABLES)
    if not exists:
        raise HTTPException(status_code=404, detail="Table not found")
    return {"ok": True, "joined": True, "table_id": table_id, "user_id": ident["user_id"]}

@router.get("/user/settings")
def user_settings(ident: Identity = Depends(get_identity)) -> Dict[str, Any]:
    return {
        "user_id": ident["user_id"],
        "theme": "auto",
        "notifications": True,
        "locale": "en",
        "currency": "chips",
        "experimental": False,
    }

@router.get("/user/stats")
def user_stats(ident: Identity = Depends(get_identity)) -> Dict[str, Any]:
    from datetime import datetime, timezone
    return {
        "user_id": ident["user_id"],
        "hands_played": 124,
        "biggest_win": 15200,
        "biggest_loss": -4800,
        "win_rate": 0.56,
        "last_played": datetime.now(timezone.utc).isoformat(),
        "streak_days": 3,
        "chip_balance": 25000,
        "rank": "Rising Shark",
    }

# Mount once at / and once at /api (so both work)
app.include_router(router)
app.include_router(router, prefix="/api")


# ---- Startup log of routes (for your diagnostics) ----
@app.on_event("startup")
async def show_routes():
    log.info("📡 Routes registered:")
    for r in app.router.routes:
        methods = getattr(r, "methods", {"GET"})
        path = getattr(r, "path", "")
        log.info("  %s %s", methods, path)
