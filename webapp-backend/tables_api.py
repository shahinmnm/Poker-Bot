"""
tables_api.py
Lightweight tables router for the Poker Telegram mini-app.

Endpoints:
  GET  /api/tables
  POST /api/tables
  POST /api/tables/{table_id}/join
  GET  /api/tables/{table_id}

DB + Auth:
  - Reuses the SQLite DB (../poker.db by default) and auth dependency from webapp_api.py
  - No schema migration tool needed; this module ensures its own tables on demand.

Mounting (next file will do this for you automatically):
  In webapp-backend/webapp_api.py:
      from tables_api import router as tables_router
      app.include_router(tables_router)
"""

from __future__ import annotations

import os
import random
import string
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import sqlite3

# Import shared helpers from your main API module
# (These exist in webapp-backend/webapp_api.py)
from webapp_api import get_conn, ensure_user, get_authed_user, AuthedUser, DB_PATH  # type: ignore


# ---------- Schema helpers (ensure tables exist) ----------

def _ensure_tables_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    # Poker tables
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tables (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            bb INTEGER NOT NULL,
            max_players INTEGER NOT NULL,
            private INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    # Seat assignments (who is seated at what table)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS table_players (
            table_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at TEXT NOT NULL,
            UNIQUE(table_id, user_id) ON CONFLICT IGNORE
        );
    """)
    conn.commit()


# ---------- Pydantic models (API shapes) ----------

class TableSummaryOut(BaseModel):
    id: str
    name: str
    bb: int = Field(..., description="Big blind (chip unit)")
    maxPlayers: int = Field(..., alias="max_players")
    seated: int
    private: bool


class CreateTableIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    bb: int = Field(ge=1, le=100)
    maxPlayers: int = Field(ge=2, le=9, alias="max_players")
    private: bool = False

    class Config:
        allow_population_by_field_name = True


class JoinOut(BaseModel):
    success: bool
    table_id: str


class PlayerOut(BaseModel):
    id: int
    name: str
    stack: int = 200
    sittingOut: bool = False


class TableDetailOut(TableSummaryOut):
    pot: int = 0
    dealer: Optional[str] = None
    stage: str = "idle"
    players: List[PlayerOut] = []


# ---------- Router ----------

router = APIRouter(prefix="/api/tables", tags=["tables"])


def _gen_table_id() -> str:
    # Short, URL-safe id like "t9F3k2aB"
    alphabet = string.ascii_letters + string.digits
    return "t" + "".join(random.choice(alphabet) for _ in range(7))


def _row_to_summary(row: sqlite3.Row, seated: int) -> TableSummaryOut:
    return TableSummaryOut(
        id=row["id"],
        name=row["name"],
        bb=int(row["bb"]),
        max_players=int(row["max_players"]),
        seated=int(seated),
        private=bool(row["private"]),
    )


@router.get("", response_model=List[TableSummaryOut])
def list_tables(user: AuthedUser = Depends(get_authed_user)) -> List[TableSummaryOut]:
    conn = get_conn()
    _ensure_tables_schema(conn)
    # Ensure user record exists (balance, settings, etc.)
    ensure_user(conn, user.id, user.username)

    cur = conn.cursor()
    rows = cur.execute("""
        SELECT t.id, t.name, t.bb, t.max_players, t.private,
               COALESCE(cnt.c, 0) AS seated
        FROM tables t
        LEFT JOIN (
            SELECT table_id, COUNT(*) AS c
            FROM table_players
            GROUP BY table_id
        ) AS cnt ON cnt.table_id = t.id
        ORDER BY datetime(t.created_at) DESC
    """).fetchall()

    out = [_row_to_summary(r, r["seated"]) for r in rows]
    conn.close()
    return out


@router.post("", response_model=TableSummaryOut)
def create_table(payload: CreateTableIn, user: AuthedUser = Depends(get_authed_user)) -> TableSummaryOut:
    conn = get_conn()
    _ensure_tables_schema(conn)
    ensure_user(conn, user.id, user.username)

    # Normalize
    name = payload.name.strip() or "Table"
    bb = int(payload.bb)
    max_players = int(payload.maxPlayers)
    is_private = 1 if payload.private else 0

    table_id = _gen_table_id()
    now = datetime.now(timezone.utc).isoformat()

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tables (id, name, bb, max_players, private, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (table_id, name, bb, max_players, is_private, user.id, now))

    # Seat the creator by default
    cur.execute("""
        INSERT INTO table_players (table_id, user_id, joined_at)
        VALUES (?, ?, ?)
    """, (table_id, user.id, now))

    conn.commit()

    # seated count is 1 (creator)
    out = TableSummaryOut(
        id=table_id,
        name=name,
        bb=bb,
        max_players=max_players,
        seated=1,
        private=bool(is_private),
    )
    conn.close()
    return out


@router.post("/{table_id}/join", response_model=JoinOut)
def join_table(table_id: str, user: AuthedUser = Depends(get_authed_user)) -> JoinOut:
    conn = get_conn()
    _ensure_tables_schema(conn)
    ensure_user(conn, user.id, user.username)

    cur = conn.cursor()
    t = cur.execute("SELECT id, max_players FROM tables WHERE id=?", (table_id,)).fetchone()
    if not t:
        conn.close()
        raise HTTPException(status_code=404, detail="Table not found")

    seated = cur.execute("SELECT COUNT(*) AS c FROM table_players WHERE table_id=?", (table_id,)).fetchone()["c"]
    if seated >= int(t["max_players"]):
        conn.close()
        raise HTTPException(status_code=409, detail="Table is full")

    now = datetime.now(timezone.utc).isoformat()
    cur.execute("""
        INSERT INTO table_players (table_id, user_id, joined_at)
        VALUES (?, ?, ?)
    """, (table_id, user.id, now))
    conn.commit()
    conn.close()
    return JoinOut(success=True, table_id=table_id)


@router.get("/{table_id}", response_model=TableDetailOut)
def table_detail(table_id: str, user: AuthedUser = Depends(get_authed_user)) -> TableDetailOut:
    conn = get_conn()
    _ensure_tables_schema(conn)
    ensure_user(conn, user.id, user.username)

    cur = conn.cursor()
    t = cur.execute("""
        SELECT id, name, bb, max_players, private, created_at
        FROM tables WHERE id=?
    """, (table_id,)).fetchone()

    if not t:
        conn.close()
        raise HTTPException(status_code=404, detail="Table not found")

    # Build players list using usernames when possible
    rows = cur.execute("""
        SELECT tp.user_id, u.username
        FROM table_players tp
        LEFT JOIN users u ON u.id = tp.user_id
        WHERE tp.table_id=?
        ORDER BY datetime(tp.joined_at) ASC
    """, (table_id,)).fetchall()

    players = []
    for r in rows:
        uid = int(r["user_id"])
        uname = r["username"] or f"Player {uid}"
        players.append({"id": uid, "name": uname, "stack": 200, "sittingOut": False})

    # Pick a simple "dealer" label (first seated), and a demo stage/pot
    dealer = players[0]["name"] if players else None
    stage = "idle"
    pot = 0

    seated = len(players)
    detail = TableDetailOut(
        id=t["id"],
        name=t["name"],
        bb=int(t["bb"]),
        max_players=int(t["max_players"]),
        seated=seated,
        private=bool(t["private"]),
        pot=pot,
        dealer=dealer,
        stage=stage,
        players=players,
    )
    conn.close()
    return detail
