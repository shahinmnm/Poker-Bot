# webapp-backend/app/routers/tables.py

from __future__ import annotations
import os
from typing import Literal, List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

router = APIRouter(tags=["tables"])

# ------------------------------
# Helpers
# ------------------------------

def _is_debug() -> bool:
    return (os.getenv("POKERBOT_DEBUG") or os.getenv("POKERBOT_DEBUG", "0")).lower() in {"1", "true", "yes"}

def resolve_user_id(request: Request) -> int:
    """
    Resolve user identity from either:
      - Telegram WebApp header: X-Telegram-Init-Data (TODO: verify & extract real user)
      - Query param: ?user_id=...
      - Dev fallback (POKERBOT_DEBUG=1): user 1
    """
    # 1) Telegram header present? (production: verify signature & parse user id)
    init_data = request.headers.get("X-Telegram-Init-Data")
    if init_data:
        # In a future step, validate `init_data` and return the actual Telegram user ID
        # For now we just accept presence as "authenticated" and use a dummy stable id.
        return 1

    # 2) Query param
    user_id = request.query_params.get("user_id")
    if user_id:
        try:
            return int(user_id)
        except ValueError:
            pass

    # 3) Dev fallback
    if _is_debug():
        return 1

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user id / Telegram init data")

# ------------------------------
# Models
# ------------------------------

Status = Literal["waiting", "running"]

class Table(BaseModel):
    id: str
    name: str
    stakes: str
    players_count: int
    max_players: int
    is_private: bool
    status: Status

class TablesResp(BaseModel):
    tables: List[Table]

class JoinResp(BaseModel):
    ok: bool
    table_id: str
    seat: int | None = None
    message: str = "joined"

# ------------------------------
# Routes
# ------------------------------

@router.get("/tables", response_model=TablesResp)
async def list_tables() -> TablesResp:
    # Mirrors the successful response you showed in logs
    return TablesResp(
        tables=[
            Table(id="pub-1", name="Main Lobby", stakes="50/100", players_count=5, max_players=9, is_private=False, status="waiting"),
            Table(id="pub-2", name="Turbo Sit&Go", stakes="100/200", players_count=9, max_players=9, is_private=False, status="running"),
            Table(id="grp-777", name="Friends Table", stakes="10/20", players_count=3, max_players=6, is_private=True, status="waiting"),
        ]
    )

@router.post("/tables/{table_id}/join", response_model=JoinResp)
async def join_table(table_id: str, user_id: int = Depends(resolve_user_id)) -> JoinResp:
    # TODO: enforce capacity, private table keys, chip balance, etc.
    # For now we return a dummy seat to unblock the UI and confirm wiring.
    return JoinResp(ok=True, table_id=table_id, seat=3, message="joined")
