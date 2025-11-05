"""
webapp_api.py — FastAPI backend for the Poker Telegram mini-app (PROD auth)

Version 1.2.0 (File 10)
  • Keeps production-grade Telegram initData verification
  • Mounts the tables router at /api/tables (from tables_api.py / File 9)
  • Same SQLite schema; DB default lives at repo root ../poker.db

Endpoints:
  - GET  /api/user/stats
  - GET  /api/user/settings
  - POST /api/user/settings
  - POST /api/user/bonus
  - (mounted) /api/tables/*

Auth docs:
  https://core.telegram.org/bots/webapps  (validate initData on server)

Run (from inside this folder):
  cd webapp-backend
  uvicorn webapp_api:app --reload --port 8080
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import hmac
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------- Config & Paths ----------

# Bot token required for Telegram initData verification
BOT_TOKEN = os.environ.get("POKERBOT_TOKEN") or os.environ.get("BOT_TOKEN") or ""

# Set to "1" or "true" to allow local dev fallback (?user_id=) if you really need it.
ALLOW_DEV_FALLBACK = os.environ.get("POKER_DEV_ALLOW_FALLBACK", "0").lower() in {"1", "true", "yes"}

# Max age for initData auth_date (seconds). Default 1 hour.
INITDATA_MAX_AGE = int(os.environ.get("POKER_INITDATA_MAX_AGE", "3600"))

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

# ---------- Telegram initData verification ----------

class InitDataError(HTTPException):
    pass


def _derive_secret_key(bot_token: str, constant: bytes = b"WebAppData") -> bytes:
    """
    Derive secret key for WebApp initData verification:
      secret = HMAC_SHA256(key="WebAppData", msg=bot_token)
    """
    return hmac.new(constant, bot_token.encode("utf-8"), hashlib.sha256).digest()


def _calc_data_check_hash(secret_key: bytes, data_check_string: str) -> str:
    return hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()


def _build_data_check_string(qs_pairs: Dict[str, str]) -> str:
    """
    Sort all pairs (except 'hash') by key (asc) and join as "k=v" lines.
    """
    items = [(k, v) for k, v in qs_pairs.items() if k != "hash"]
    items.sort(key=lambda x: x[0])
    return "\n".join(f"{k}={v}" for k, v in items)


def verify_init_data(init_data_raw: str, bot_token: str, max_age_seconds: int = 3600) -> Dict[str, str]:
    """
    Validates Telegram WebApp initData string per docs.
    Returns parsed dict (keys are already percent-decoded) if valid; else raises.
    """
    if not bot_token:
        raise InitDataError(status_code=500, detail="Server misconfig: POKERBOT_TOKEN is not set")

    # Parse the raw querystring exactly as delivered by Telegram
    try:
        parsed = dict(parse_qsl(init_data_raw, keep_blank_values=True, strict_parsing=True))
    except Exception:
        raise InitDataError(status_code=400, detail="Invalid initData format")

    init_hash = parsed.get("hash")
    if not init_hash:
        raise InitDataError(status_code=400, detail="Missing hash in initData")

    # Build the data_check_string and compute expected hash
    secret = _derive_secret_key(bot_token)
    dcs = _build_data_check_string(parsed)
    expected_hash = _calc_data_check_hash(secret, dcs)

    # Timing-safe compare
    if not hmac.compare_digest(expected_hash, init_hash):
        raise InitDataError(status_code=401, detail="Bad initData signature")

    # Optional freshness check via auth_date
    try:
        auth_date = int(parsed.get("auth_date", "0"))
    except ValueError:
        auth_date = 0
    if auth_date:
        now = int(datetime.now(timezone.utc).timestamp())
        if now - auth_date > max_age_seconds:
            raise InitDataError(status_code=401, detail="initData expired")

    return parsed

# ---------- Auth dependency ----------

@dataclass
class AuthedUser:
    id: int
    username: Optional[str] = None


async def get_authed_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> AuthedUser:
    """
    Production: require Authorization: Bearer <window.Telegram.WebApp.initData>
    Dev fallback (?user_id=) only if ALLOW_DEV_FALLBACK=1.
    """
    if authorization and authorization.lower().startswith("bearer "):
        init_data_raw = authorization.split(" ", 1)[1].strip()
        parsed = verify_init_data(init_data_raw, BOT_TOKEN, INITDATA_MAX_AGE)
        # "user" comes JSON-encoded in initData "user={...}" (already decoded by parse_qsl)
        user_json_raw = parsed.get("user")
        user_id: Optional[int] = None
        username: Optional[str] = None
        if user_json_raw:
            try:
                user_obj = json.loads(user_json_raw)
                if "id" in user_obj:
                    user_id = int(user_obj["id"])
                username = user_obj.get("username")
            except Exception:
                pass
        if not user_id:
            raise InitDataError(status_code=400, detail="No user.id in initData")
        conn = get_conn()
        ensure_user(conn, user_id, username)
        conn.close()
        return AuthedUser(id=user_id, username=username)

    # Dev-only escape hatch
    if ALLOW_DEV_FALLBACK:
        try:
            q_uid = request.query_params.get("user_id")
            if q_uid:
                user_id = int(q_uid)
                conn = get_conn()
                ensure_user(conn, user_id, "demo")
                conn.close()
                return AuthedUser(id=user_id, username="demo")
        except Exception:
            pass

    raise InitDataError(status_code=401, detail="Authorization required")

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

app = FastAPI(title="Poker WebApp API", version="1.2.0")

# CORS: allow your frontend origin(s) during development
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

# ---------- User Endpoints ----------

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

# ---------- Mount tables router (/api/tables) ----------

# NOTE: import placed AFTER helpers & app exist to avoid circular-import issues.
from tables_api import router as tables_router  # type: ignore

app.include_router(tables_router)

# ---------- Entrypoint ----------

if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        init_db()
        # Optional: seed demo user on first run
        conn = get_conn()
        ensure_user(conn, 1, "demo")
        conn.close()

    # IMPORTANT: run from INSIDE webapp-backend
    #   cd webapp-backend
    #   uvicorn webapp_api:app --reload --port 8080
    import uvicorn
    uvicorn.run("webapp_api:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), reload=True)
