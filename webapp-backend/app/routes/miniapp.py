# webapp-backend/app/routers/miniapp.py
from __future__ import annotations

import json, os, sqlite3, hmac, hashlib, random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

# ---------- Config / DB ----------
BOT_TOKEN = os.environ.get("POKERBOT_TOKEN", "")
ALLOW_DEV_FALLBACK = os.environ.get("POKER_DEV_ALLOW_FALLBACK", "0").lower() in {"1","true","yes"}
INITDATA_MAX_AGE = int(os.environ.get("POKER_INITDATA_MAX_AGE", "3600"))

# repo root sqlite (../.. from this file)
_DEFAULT_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "poker.db"))
DB_PATH = os.environ.get("POKER_DB_PATH", _DEFAULT_DB)

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _init_db_once() -> None:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, username TEXT, balance INTEGER NOT NULL DEFAULT 1000);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
        user_id INTEGER PRIMARY KEY,
        fourColorDeck INTEGER NOT NULL DEFAULT 1,
        showHandStrength INTEGER NOT NULL DEFAULT 1,
        confirmAllIn INTEGER NOT NULL DEFAULT 1,
        autoCheckFold INTEGER NOT NULL DEFAULT 0,
        haptics INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS stats (
        user_id INTEGER PRIMARY KEY,
        hands_played INTEGER NOT NULL DEFAULT 0,
        hands_won INTEGER NOT NULL DEFAULT 0,
        total_profit INTEGER NOT NULL DEFAULT 0,
        biggest_pot_won INTEGER NOT NULL DEFAULT 0,
        avg_stake INTEGER NOT NULL DEFAULT 0,
        current_streak INTEGER NOT NULL DEFAULT 0,
        hand_distribution TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS bonus_claims (
        user_id INTEGER PRIMARY KEY, last_claim_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);""")
    # tables feature
    cur.execute("""CREATE TABLE IF NOT EXISTS tables (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, bb INTEGER NOT NULL,
        max_players INTEGER NOT NULL, private INTEGER NOT NULL DEFAULT 0,
        created_by INTEGER NOT NULL, created_at TEXT NOT NULL);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS table_players (
        table_id TEXT NOT NULL, user_id INTEGER NOT NULL, joined_at TEXT NOT NULL,
        UNIQUE(table_id, user_id) ON CONFLICT IGNORE);""")
    conn.commit(); conn.close()
_init_db_once()

def ensure_user(conn: sqlite3.Connection, user_id: int, username: Optional[str]) -> None:
    cur = conn.cursor()
    if not cur.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        cur.execute("INSERT INTO users (id, username, balance) VALUES (?, ?, ?)", (user_id, username, 1000))
        cur.execute("INSERT OR IGNORE INTO settings (user_id) VALUES (?)", (user_id,))
        cur.execute("INSERT OR IGNORE INTO stats (user_id, hand_distribution) VALUES (?, ?)",
                    (user_id, json.dumps({
                        "High Card":0,"Pair":0,"Two Pair":0,"Three of a Kind":0,
                        "Straight":0,"Flush":0,"Full House":0,"Four of a Kind":0,"Straight Flush":0
                    })))
        cur.execute("INSERT OR IGNORE INTO bonus_claims (user_id,last_claim_at) VALUES (?,?)", (user_id, None))
        conn.commit()

# ---------- Auth (Telegram WebApp initData) ----------

class InitDataError(HTTPException): ...

def _secret(bot_token: str, constant: bytes = b"WebAppData") -> bytes:
    return hmac.new(constant, bot_token.encode("utf-8"), hashlib.sha256).digest()

def _calc(secret: bytes, data_check_string: str) -> str:
    return hmac.new(secret, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

def _dcs(pairs: Dict[str,str]) -> str:
    items = [(k,v) for k,v in pairs.items() if k != "hash"]; items.sort(key=lambda x:x[0])
    return "\n".join(f"{k}={v}" for k,v in items)

@dataclass
class AuthedUser:
    id: int
    username: Optional[str] = None

async def get_authed_user(request: Request, authorization: Optional[str] = Header(default=None)) -> AuthedUser:
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization.split(" ", 1)[1].strip()
        try:
            parsed = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=True))
        except Exception:
            raise InitDataError(status_code=400, detail="Invalid initData")
        init_hash = parsed.get("hash")
        if not BOT_TOKEN:
            # When not configured, allow only if dev fallback is enabled
            if not ALLOW_DEV_FALLBACK: raise InitDataError(status_code=500, detail="BOT token not set")
        if BOT_TOKEN:
            secret = _secret(BOT_TOKEN)
            if not init_hash or not hmac.compare_digest(_calc(secret, _dcs(parsed)), init_hash):
                raise InitDataError(status_code=401, detail="Bad initData signature")
            # freshness
            try:
                auth_ts = int(parsed.get("auth_date", "0"))
                if auth_ts and int(datetime.now(timezone.utc).timestamp()) - auth_ts > INITDATA_MAX_AGE:
                    raise InitDataError(status_code=401, detail="initData expired")
            except ValueError:
                pass
        # user json is in parsed["user"]
        uid, uname = None, None
        try:
            uj = parsed.get("user")
            if uj:
                uo = json.loads(uj)
                uid = int(uo.get("id")) if uo.get("id") is not None else None
                uname = uo.get("username")
        except Exception:
            pass
        if not uid: raise InitDataError(status_code=400, detail="No user.id in initData")
        conn = get_conn(); ensure_user(conn, uid, uname); conn.close()
        return AuthedUser(id=uid, username=uname)

    if ALLOW_DEV_FALLBACK:
        try:
            q_uid = request.query_params.get("user_id")
            if q_uid:
                uid = int(q_uid); conn = get_conn(); ensure_user(conn, uid, "demo"); conn.close()
                return AuthedUser(id=uid, username="demo")
        except Exception:
            pass
    raise InitDataError(status_code=401, detail="Authorization required")

# ---------- Schemas ----------
class StatsOut(BaseModel):
    hands_played:int; hands_won:int; total_profit:int; biggest_pot_won:int
    avg_stake:int; current_streak:int; hand_distribution:Dict[str,int]=Field(default_factory=dict)

class SettingsIn(BaseModel):
    fourColorDeck:bool=True; showHandStrength:bool=True; confirmAllIn:bool=True; autoCheckFold:bool=False; haptics:bool=True
class SettingsOut(SettingsIn):
    balance: Optional[int]=None

class BonusOut(BaseModel):
    success:bool; amount:Optional[int]=None; next_claim_at:Optional[str]=None; message:Optional[str]=None

class TableSummaryOut(BaseModel):
    id:str; name:str; bb:int; maxPlayers:int=Field(..., alias="max_players"); seated:int; private:bool
    class Config: allow_population_by_field_name=True
class CreateTableIn(BaseModel):
    name:str=Field(min_length=1, max_length=40); bb:int=Field(ge=1, le=100)
    maxPlayers:int=Field(ge=2, le=9, alias="max_players"); private:bool=False
    class Config: allow_population_by_field_name=True
class PlayerOut(BaseModel):
    id:int; name:str; stack:int=200; sittingOut:bool=False
class TableDetailOut(TableSummaryOut):
    pot:int=0; dealer:Optional[str]=None; stage:str="idle"; players:List[PlayerOut]=[]

# ---------- Router (no /api prefix; include with and without in main) ----------
router = APIRouter()

# -- User endpoints
@router.get("/user/stats", response_model=StatsOut)
def get_stats(user: AuthedUser = Depends(get_authed_user)) -> StatsOut:
    conn = get_conn(); cur = conn.cursor()
    row = cur.execute("""SELECT hands_played,hands_won,total_profit,biggest_pot_won,avg_stake,current_streak,hand_distribution
                         FROM stats WHERE user_id=?""",(user.id,)).fetchone()
    if not row:
        ensure_user(conn, user.id, user.username)
        row = cur.execute("""SELECT hands_played,hands_won,total_profit,biggest_pot_won,avg_stake,current_streak,hand_distribution
                             FROM stats WHERE user_id=?""",(user.id,)).fetchone()
    try: dist = json.loads(row["hand_distribution"] or "{}")
    except Exception: dist = {}
    out = StatsOut(hands_played=row["hands_played"], hands_won=row["hands_won"], total_profit=row["total_profit"],
                   biggest_pot_won=row["biggest_pot_won"], avg_stake=row["avg_stake"],
                   current_streak=row["current_streak"], hand_distribution=dist)
    conn.close(); return out

@router.get("/user/settings", response_model=SettingsOut)
def get_settings(user: AuthedUser = Depends(get_authed_user)) -> SettingsOut:
    conn = get_conn(); cur = conn.cursor()
    s = cur.execute("""SELECT fourColorDeck,showHandStrength,confirmAllIn,autoCheckFold,haptics FROM settings WHERE user_id=?""",(user.id,)).fetchone()
    u = cur.execute("SELECT balance FROM users WHERE id=?", (user.id,)).fetchone()
    if not s:
        ensure_user(conn, user.id, user.username)
        s = cur.execute("""SELECT fourColorDeck,showHandStrength,confirmAllIn,autoCheckFold,haptics FROM settings WHERE user_id=?""",(user.id,)).fetchone()
        u = cur.execute("SELECT balance FROM users WHERE id=?", (user.id,)).fetchone()
    conn.close()
    return SettingsOut(fourColorDeck=bool(s["fourColorDeck"]), showHandStrength=bool(s["showHandStrength"]),
                       confirmAllIn=bool(s["confirmAllIn"]), autoCheckFold=bool(s["autoCheckFold"]),
                       haptics=bool(s["haptics"]), balance=int(u["balance"]) if u else 0)

@router.post("/user/settings", response_model=SettingsOut)
def update_settings(payload: SettingsIn, user: AuthedUser = Depends(get_authed_user)) -> SettingsOut:
    conn = get_conn(); cur = conn.cursor(); ensure_user(conn, user.id, user.username)
    cur.execute("""UPDATE settings SET fourColorDeck=?,showHandStrength=?,confirmAllIn=?,autoCheckFold=?,haptics=? WHERE user_id=?""",
                (1 if payload.fourColorDeck else 0, 1 if payload.showHandStrength else 0,
                 1 if payload.confirmAllIn else 0, 1 if payload.autoCheckFold else 0,
                 1 if payload.haptics else 0, user.id))
    conn.commit()
    bal = cur.execute("SELECT balance FROM users WHERE id=?", (user.id,)).fetchone()
    conn.close(); return SettingsOut(**payload.dict(), balance=int(bal["balance"]) if bal else 0)

@router.post("/user/bonus", response_model=BonusOut)
def claim_bonus(user: AuthedUser = Depends(get_authed_user)) -> BonusOut:
    conn = get_conn(); cur = conn.cursor(); ensure_user(conn, user.id, user.username)
    row = cur.execute("SELECT last_claim_at FROM bonus_claims WHERE user_id=?", (user.id,)).fetchone()
    now = datetime.now(timezone.utc)
    if row and row["last_claim_at"]:
        try: last = datetime.fromisoformat(row["last_claim_at"])
        except Exception: last = now - timedelta(days=2)
    else: last = now - timedelta(days=2)
    if now - last < timedelta(hours=24):
        conn.close(); return BonusOut(success=False, next_claim_at=(last+timedelta(hours=24)).isoformat(),
                                      message="Bonus already claimed. Come back later!")
    amount = random.randint(100, 300)
    cur.execute("UPDATE users SET balance=balance+? WHERE id=?", (amount, user.id))
    cur.execute("UPDATE bonus_claims SET last_claim_at=? WHERE user_id=?", (now.isoformat(), user.id))
    conn.commit(); conn.close()
    return BonusOut(success=True, amount=amount, next_claim_at=(now+timedelta(hours=24)).isoformat(), message="Bonus claimed!")

# -- Tables endpoints
def _row_to_summary(row: sqlite3.Row, seated: int) -> TableSummaryOut:
    return TableSummaryOut(id=row["id"], name=row["name"], bb=int(row["bb"]),
                           max_players=int(row["max_players"]), seated=int(seated), private=bool(row["private"]))

@router.get("/tables", response_model=List[TableSummaryOut])
def list_tables(user: AuthedUser = Depends(get_authed_user)) -> List[TableSummaryOut]:
    conn = get_conn(); ensure_user(conn, user.id, user.username); cur = conn.cursor()
    rows = cur.execute("""SELECT t.id,t.name,t.bb,t.max_players,t.private, COALESCE(cnt.c,0) AS seated
                          FROM tables t
                          LEFT JOIN (SELECT table_id,COUNT(*) c FROM table_players GROUP BY table_id) cnt
                          ON cnt.table_id=t.id ORDER BY datetime(t.created_at) DESC""").fetchall()
    out = [_row_to_summary(r, r["seated"]) for r in rows]; conn.close(); return out

@router.post("/tables", response_model=TableSummaryOut)
def create_table(payload: CreateTableIn, user: AuthedUser = Depends(get_authed_user)) -> TableSummaryOut:
    conn = get_conn(); ensure_user(conn, user.id, user.username); cur = conn.cursor()
    import string, random as rnd
    table_id = "t" + "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(7))
    name = (payload.name or "Table").strip(); bb = int(payload.bb); maxp = int(payload.maxPlayers); priv = 1 if payload.private else 0
    now = datetime.now(timezone.utc).isoformat()
    cur.execute("""INSERT INTO tables (id,name,bb,max_players,private,created_by,created_at)
                   VALUES (?,?,?,?,?,?,?)""", (table_id,name,bb,maxp,priv,user.id,now))
    cur.execute("""INSERT INTO table_players (table_id,user_id,joined_at) VALUES (?,?,?)""",(table_id,user.id,now))
    conn.commit(); conn.close()
    return TableSummaryOut(id=table_id, name=name, bb=bb, max_players=maxp, seated=1, private=bool(priv))

class JoinOut(BaseModel): success: bool; table_id: str

@router.post("/tables/{table_id}/join", response_model=JoinOut)
def join_table(table_id: str, user: AuthedUser = Depends(get_authed_user)) -> JoinOut:
    conn = get_conn(); ensure_user(conn, user.id, user.username); cur = conn.cursor()
    t = cur.execute("SELECT id,max_players FROM tables WHERE id=?", (table_id,)).fetchone()
    if not t: conn.close(); raise HTTPException(status_code=404, detail="Table not found")
    seated = cur.execute("SELECT COUNT(*) c FROM table_players WHERE table_id=?", (table_id,)).fetchone()["c"]
    if seated >= int(t["max_players"]): conn.close(); raise HTTPException(status_code=409, detail="Table is full")
    now = datetime.now(timezone.utc).isoformat()
    cur.execute("INSERT INTO table_players (table_id,user_id,joined_at) VALUES (?,?,?)", (table_id, user.id, now))
    conn.commit(); conn.close(); return JoinOut(success=True, table_id=table_id)

@router.get("/tables/{table_id}", response_model=TableDetailOut)
def table_detail(table_id: str, user: AuthedUser = Depends(get_authed_user)) -> TableDetailOut:
    conn = get_conn(); ensure_user(conn, user.id, user.username); cur = conn.cursor()
    t = cur.execute("SELECT id,name,bb,max_players,private FROM tables WHERE id=?", (table_id,)).fetchone()
    if not t: conn.close(); raise HTTPException(status_code=404, detail="Table not found")
    rows = cur.execute("""SELECT tp.user_id,u.username FROM table_players tp
                          LEFT JOIN users u ON u.id=tp.user_id WHERE tp.table_id=? ORDER BY datetime(tp.joined_at) ASC""",
                       (table_id,)).fetchall()
    players = [{"id": int(r["user_id"]), "name": (r["username"] or f"Player {r['user_id']}"), "stack": 200, "sittingOut": False}
               for r in rows]
    dealer = players[0]["name"] if players else None
    detail = TableDetailOut(id=t["id"], name=t["name"], bb=int(t["bb"]), max_players=int(t["max_players"]),
                            seated=len(players), private=bool(t["private"]),
                            pot=0, dealer=dealer, stage="idle", players=players)
    conn.close(); return detail
