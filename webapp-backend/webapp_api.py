"""
webapp_api.py  — FastAPI backend for the Poker Telegram mini-app.

Endpoints (all JSON):
  GET  /api/user/stats
  GET  /api/user/settings
  POST /api/user/settings
  POST /api/user/bonus

Auth model (dev-friendly):
- Front-end sends Authorization: Bearer <Telegram initData> (string).
- For production, replace dev parsing with proper Telegram initData verification:
  https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app

Repo-aware notes (IMPORTANT):
- This file belongs under:  webapp-backend/webapp_api.py
- Because the folder name has a hyphen, import paths like "webapp-backend.webapp_api:app"
  will NOT work. Run uvicorn from inside the directory instead:

  cd webapp-backend
  uvicorn webapp_api:app --reload --port 8080

SQLite file:
- Default location: repo root (../poker.db relative to this file), so both bot & API can share it.
- Override with env var POKER_DB_PATH if you prefer a different location.

Front-end:
- The React mini-app (Stats / Account panels) already calls:
    GET  /api/user/stats
    GET  /api/user/settings
    POST /api/user/settings
    POST /api/user/bonus
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import urllib.parse

# ---------- Paths & DB ----------

# Default DB in repo root (../poker.db relative to this file)
_DEFAULT_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "poker.db"))
DB_PATH = os.environ.get("POKER_DB_PATH", _DEFAULT_DB)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER NOT NULL DEFAULT 1000
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            fourColorDeck INTEGER NOT NULL DEFAULT 1,
            showHandStrength INTEGER NOT NULL DEFAULT 1,
            confirmAllIn INTEGER NOT NULL DEFAULT 1,
            autoCheckFold INTEGER NOT NULL DEFAULT 0,
            haptics INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            user_id INTEGER PRIMARY KEY,
            hands_played INTEGER NOT NULL DEFAULT 0,
            hands_won INTEGER NOT NULL DEFAULT 0,
            total_profit INTEGER NOT NULL DEFAULT 0,
            biggest_pot_won INTEGER NOT NULL DEFAULT 0,
            avg_stake INTEGER NOT NULL DEFAULT 0,
            current_streak INTEGER NOT NULL DEFAULT 0,
            hand_distribution TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bonus_claims (
            user_id INTEGER PRIMARY KEY,
            last_claim_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


def ensure_user(conn: sqlite3.Connection, user_id: int, username: Optional[str]) -> None:
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE id=?", (user_id,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (id, username, balance) VALUES (?, ?, ?)",
            (user_id, username, 1000),
        )
        cur.execute("INSERT OR IGNORE INTO settings (user_id) VALUES (?)", (user_id,))
        cur.execute(
            "INSERT OR IGNORE INTO stats (user_id, hand_distribution) VALUES (?, ?)",
            (user_id, json.dumps({
                "High Card": 0, "Pair": 0, "Two Pair": 0, "Three of a Kind": 0,
                "Straight": 0, "Flush": 0, "Full House": 0, "Four of a Kind": 0,
                "Straight Flush": 0
            })),
        )
        cur.execute(
            "INSERT OR IGNORE INTO bonus_claims (user_id, last_claim_at) VALUES (?, ?)",
            (user_id, None),
        )
        conn.commit()


# ---------- Dev Auth Helpers ----------

@dataclass
class AuthedUser:
    id: int
    username: Optional[str] = None


def _parse_user_from_initdata(init_data: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Parse Telegram WebApp initData and extract user.id and user.username.
    Dev-only convenience; replace with strict verification for production.
    initData looks like: "query_id=...&user=%7B...%7D&auth_date=...&hash=..."
    """
    try:
        parts = urllib.parse.parse_qs(init_data, keep_blank_values=True)
        user_raw = parts.get("user", [None])[0]
        if not user_raw:
            return None, None
        user_json = json.loads(user_raw)  # it's URL-decoded JSON from TG
        uid = int(user_json.get("id")) if user_json.get("id") is not None else None
        uname = user_json.get("username")
        return uid, uname
    except Exception:
        return None, None


async def get_authed_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> AuthedUser:
    """
    Dev behavior:
      - Try Authorization: Bearer <initData>
      - Fallback to ?user_id=<int>
      - Final fallback: user #1 'demo'
    """
    uid: Optional[int] = None
    uname: Optional[str] = None

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        uid, uname = _parse_user_from_initdata(token)

    if uid is None:
        try:
            q_uid = request.query_params.get("user_id")
            if q_uid:
                uid = int(q_uid)
        except Exception:
            uid = None

    if uid is None:
        uid = 1
        uname = uname or "demo"

    conn = get_conn()
    ensure_user(conn, uid, uname)
    conn.close()
    return AuthedUser(id=uid, username=uname)


# ---------- Schemas ----------

class StatsOut(BaseModel):
    hands_played: int
    hands_won: int
    total_profit: int
    biggest_pot_won: int
    avg_stake: int
    current_streak: int
    hand_distribution: Dict[str, int] = Field(default_factory=dict)


class SettingsIn(BaseModel):
    fourColorDeck: bool = True
    showHandStrength: bool = True
    confirmAllIn: bool = True
    autoCheckFold: bool = False
    haptics: bool = True


class SettingsOut(SettingsIn):
    balance: Optional[int] = None


class BonusOut(BaseModel):
    success: bool
    amount: Optional[int] = None
    next_claim_at: Optional[str] = None
    message: Optional[str] = None


# ---------- FastAPI ----------

app = FastAPI(title="Poker WebApp API", version="1.0.0")

# CORS: during development you can allow all; restrict in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("POKER_CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------- Endpoints ----------

@app.get("/api/user/stats", response_model=StatsOut)
def get_stats(user: AuthedUser = Depends(get_authed_user)) -> StatsOut:
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute("""
        SELECT hands_played, hands_won, total_profit, biggest_pot_won,
               avg_stake, current_streak, hand_distribution
        FROM stats WHERE user_id=?
    """, (user.id,)).fetchone()

    if not row:
        ensure_user(conn, user.id, user.username)
        row = cur.execute("""
            SELECT hands_played, hands_won, total_profit, biggest_pot_won,
                   avg_stake, current_streak, hand_distribution
            FROM stats WHERE user_id=?
        """, (user.id,)).fetchone()

    try:
        dist = json.loads(row["hand_distribution"] or "{}")
    except Exception:
        dist = {}

    out = StatsOut(
        hands_played=row["hands_played"],
        hands_won=row["hands_won"],
        total_profit=row["total_profit"],
        biggest_pot_won=row["biggest_pot_won"],
        avg_stake=row["avg_stake"],
        current_streak=row["current_streak"],
        hand_distribution=dist,
    )
    conn.close()
    return out


@app.get("/api/user/settings", response_model=SettingsOut)
def get_settings(user: AuthedUser = Depends(get_authed_user)) -> SettingsOut:
    conn = get_conn()
    cur = conn.cursor()
    s = cur.execute("""
        SELECT fourColorDeck, showHandStrength, confirmAllIn, autoCheckFold, haptics
        FROM settings WHERE user_id=?
    """, (user.id,)).fetchone()
    u = cur.execute("SELECT balance FROM users WHERE id=?", (user.id,)).fetchone()

    if not s:
        ensure_user(conn, user.id, user.username)
        s = cur.execute("""
            SELECT fourColorDeck, showHandStrength, confirmAllIn, autoCheckFold, haptics
            FROM settings WHERE user_id=?
        """, (user.id,)).fetchone()
        u = cur.execute("SELECT balance FROM users WHERE id=?", (user.id,)).fetchone()

    conn.close()
    return SettingsOut(
        fourColorDeck=bool(s["fourColorDeck"]),
        showHandStrength=bool(s["showHandStrength"]),
        confirmAllIn=bool(s["confirmAllIn"]),
        autoCheckFold=bool(s["autoCheckFold"]),
        haptics=bool(s["haptics"]),
        balance=int(u["balance"]) if u else 0,
    )


@app.post("/api/user/settings", response_model=SettingsOut)
def update_settings(payload: SettingsIn, user: AuthedUser = Depends(get_authed_user)) -> SettingsOut:
    conn = get_conn()
    cur = conn.cursor()
    ensure_user(conn, user.id, user.username)

    cur.execute("""
        UPDATE settings
        SET fourColorDeck=?, showHandStrength=?, confirmAllIn=?, autoCheckFold=?, haptics=?
        WHERE user_id=?
    """, (
        1 if payload.fourColorDeck else 0,
        1 if payload.showHandStrength else 0,
        1 if payload.confirmAllIn else 0,
        1 if payload.autoCheckFold else 0,
        1 if payload.haptics else 0,
        user.id,
    ))
    conn.commit()

    bal = cur.execute("SELECT balance FROM users WHERE id=?", (user.id,)).fetchone()
    conn.close()
    return SettingsOut(**payload.dict(), balance=int(bal["balance"]) if bal else 0)


@app.post("/api/user/bonus", response_model=BonusOut)
def claim_bonus(user: AuthedUser = Depends(get_authed_user)) -> BonusOut:
    conn = get_conn()
    cur = conn.cursor()
    ensure_user(conn, user.id, user.username)

    row = cur.execute("SELECT last_claim_at FROM bonus_claims WHERE user_id=?", (user.id,)).fetchone()
    now = datetime.now(timezone.utc)

    if row and row["last_claim_at"]:
        try:
            last = datetime.fromisoformat(row["last_claim_at"])
        except Exception:
            last = now - timedelta(days=2)
    else:
        last = now - timedelta(days=2)

    if now - last < timedelta(hours=24):
        next_claim_at = (last + timedelta(hours=24)).isoformat()
        conn.close()
        return BonusOut(success=False, next_claim_at=next_claim_at, message="Bonus already claimed. Come back later!")

    # Grant bonus
    amount = random.randint(100, 300)
    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user.id))
    cur.execute("UPDATE bonus_claims SET last_claim_at=? WHERE user_id=?", (now.isoformat(), user.id))
    conn.commit()
    conn.close()

    return BonusOut(success=True, amount=amount, next_claim_at=(now + timedelta(hours=24)).isoformat(), message="Bonus claimed!")


# ---------- Demo seeding (optional) ----------

def _seed_demo_progress(user_id: int = 1) -> None:
    """Optional helper to seed some believable stats."""
    conn = get_conn()
    ensure_user(conn, user_id, "demo")
    cur = conn.cursor()
    dist = {
        "High Card": 110, "Pair": 96, "Two Pair": 58, "Three of a Kind": 28,
        "Straight": 18, "Flush": 14, "Full House": 12, "Four of a Kind": 5,
        "Straight Flush": 1
    }
    cur.execute("""
        UPDATE stats SET
          hands_played=?, hands_won=?, total_profit=?, biggest_pot_won=?,
          avg_stake=?, current_streak=?, hand_distribution=?
        WHERE user_id=?
    """, (342, 97, 1520, 640, 2, 3, json.dumps(dist), user_id))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    # Initialize DB and optionally seed a demo user on first run
    if not os.path.exists(DB_PATH):
        init_db()
        _seed_demo_progress(1)

    # IMPORTANT: Run from INSIDE webapp-backend due to hyphen in folder name.
    # Example:
    #   cd webapp-backend
    #   uvicorn webapp_api:app --reload --port 8080
    import uvicorn
    uvicorn.run("webapp_api:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), reload=True)
