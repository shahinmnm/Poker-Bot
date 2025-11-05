import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import FastAPI, APIRouter, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

log = logging.getLogger("app.main")
logging.basicConfig(level=logging.INFO, format="%(levelname)s:app.main:%(message)s")

# ------------------------------------------------------------------------------
# Mock in-memory state (replace with real storage later)
# ------------------------------------------------------------------------------
TABLES: Dict[str, Dict[str, Any]] = {
    "pub-1": {"id": "pub-1", "name": "Main Lobby",      "stakes": "50/100",  "players_count": 5, "max_players": 9, "is_private": False, "status": "waiting"},
    "pub-2": {"id": "pub-2", "name": "Turbo Sit&Go",    "stakes": "100/200", "players_count": 9, "max_players": 9, "is_private": False, "status": "running"},
    "grp-777": {"id": "grp-777", "name": "Friends Table","stakes": "10/20",   "players_count": 3, "max_players": 6, "is_private": True,  "status": "waiting"},
}

DEFAULT_SETTINGS = {
    "theme": "auto",
    "notifications": True,
    "locale": "en",
    "currency": "chips",
    "experimental": False,
}

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")

# ------------------------------------------------------------------------------
# Identity helper: outside Telegram, require ?user_id=... (UI wrapper appends user_id=1)
# ------------------------------------------------------------------------------
def _require_user_id(request: Request) -> int:
    # 1) Query param (dev/desktop path)
    user_id = request.query_params.get("user_id")
    if user_id:
        try:
            return int(user_id)
        except ValueError:
            pass

    # 2) Telegram WebApp header (production path). You can implement validation later.
    # x_init = request.headers.get("X-Telegram-Init-Data")
    # if x_init: ... validate and derive a user id ...

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing user identity")

# ------------------------------------------------------------------------------
# FastAPI app & CORS
# ------------------------------------------------------------------------------
app = FastAPI(title="Poker WebApp API", version="0.1.0")

cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if not cors_origins:
    # Default to same-origin + localhost for dev
    cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000", "https://poker.shahin8n.sbs"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")

# ------------------------------------------------------------------------------
# Route impls (single functions; we register them both on "" and "/api")
# ------------------------------------------------------------------------------
async def health() -> Dict[str, Any]:
    return {"status": "ok", "time": _now_iso()}

async def list_tables() -> Dict[str, Any]:
    return {"tables": list(TABLES.values())}

async def user_settings(request: Request) -> Dict[str, Any]:
    user_id = _require_user_id(request)
    return {"user_id": user_id, **DEFAULT_SETTINGS}

async def user_stats(request: Request) -> Dict[str, Any]:
    user_id = _require_user_id(request)
    # demo data
    return {
        "user_id": user_id,
        "hands_played": 124,
        "biggest_win": 15200,
        "biggest_loss": -4800,
        "win_rate": 0.56,
        "last_played": _now_iso(),
        "streak_days": 3,
        "chip_balance": 25000,
        "rank": "Rising Shark",
    }

async def join_table(table_id: str, request: Request) -> Dict[str, Any]:
    user_id = _require_user_id(request)
    tbl = TABLES.get(table_id)
    if not tbl:
        raise HTTPException(status_code=404, detail="Table not found")

    # naive capacity check
    if tbl["players_count"] >= tbl["max_players"]:
        raise HTTPException(status_code=409, detail="Table is full")

    # pretend to put user into the table (in-memory)
    tbl["players_count"] += 1
    seat_number = tbl["players_count"]  # simplistic
    return {
        "ok": True,
        "table_id": tbl["id"],
        "user_id": user_id,
        "joined_at": _now_iso(),
        "seat": seat_number,
        "table": tbl,
    }

# ------------------------------------------------------------------------------
# Registration helper: mount endpoints on a router (app and /api)
# ------------------------------------------------------------------------------
def register_routes(r: APIRouter | FastAPI) -> None:
    r.add_api_route("/health", health, methods=["GET"])
    r.add_api_route("/tables", list_tables, methods=["GET"])
    r.add_api_route("/user/settings", user_settings, methods=["GET"])
    r.add_api_route("/user/stats", user_stats, methods=["GET"])
    r.add_api_route("/tables/{table_id}/join", join_table, methods=["POST"])

register_routes(app)
register_routes(api)
app.include_router(api)

# ------------------------------------------------------------------------------
# Log visible routes at startup
# ------------------------------------------------------------------------------
@app.on_event("startup")
async def _log_routes():
    log.info("🚀 Poker WebApp API starting...")
    log.info("📍 CORS origins: %s", cors_origins)
    log.info("📡 Routes registered:")
    for route in app.router.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            log.info("  %s %s", route.methods, route.path)
