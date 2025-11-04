from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.dependencies import get_current_user, get_redis_client
from app.models import User
from app.utils.poker import deal_cards, process_action

router = APIRouter(prefix="/game", tags=["game"])


class CreateGameRequest(BaseModel):
    stake: str
    mode: str = "group"


class JoinGameRequest(BaseModel):
    game_id: str


class GameActionRequest(BaseModel):
    game_id: str
    action: str
    amount: Optional[int] = None


class ReadyRequest(BaseModel):
    game_id: str


class GameResponse(BaseModel):
    id: str
    stake: str
    player_count: int
    mode: str
    status: str
    min_players: int = 2
    max_players: int = 9


@router.get("/list")
async def list_games(
    request: Request,
    user: User = Depends(get_current_user),
    redis=Depends(get_redis_client),
):
    """List all active games."""
    try:
        games: list[dict] = []
        cursor = 0

        while True:
            cursor, keys = redis.scan(cursor=cursor, match="game:*:meta", count=100)

            for key in keys:
                game_id = key.split(":")[1]
                meta_data = redis.get(key)

                if not meta_data:
                    continue

                meta = json.loads(meta_data)
                player_keys = redis.keys(f"game:{game_id}:player:*")

                games.append(
                    {
                        "id": game_id,
                        "stake": meta.get("stake", "1/2"),
                        "player_count": len(player_keys),
                        "mode": meta.get("mode", "group"),
                        "status": meta.get("status", "waiting"),
                    }
                )

            if cursor == 0:
                break

        return {"games": games}

    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/create")
async def create_game(
    request: CreateGameRequest,
    user: User = Depends(get_current_user),
    redis=Depends(get_redis_client),
):
    """Create a new poker game."""
    try:
        try:
            small_blind_str, big_blind_str = request.stake.split("/")
            small_blind = int(small_blind_str)
            big_blind = int(big_blind_str)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid stake format. Use 'small/big'.")

        game_id = str(uuid.uuid4())
        meta = {
            "stake": request.stake,
            "mode": request.mode,
            "status": "waiting",
            "creator_id": user.id,
            "created_at": datetime.now().isoformat(),
        }

        redis.set(f"game:{game_id}:meta", json.dumps(meta), ex=7200)

        initial_state = {
            "game_id": game_id,
            "players": [],
            "pot": 0,
            "community_cards": [],
            "current_turn": -1,
            "phase": "waiting",
            "dealer_index": 0,
            "small_blind": small_blind,
            "big_blind": big_blind,
            "ready_players": [],
        }

        redis.set(f"game:{game_id}:state", json.dumps(initial_state), ex=7200)

        return {"game_id": game_id, "status": "created"}

    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/join")
async def join_game(
    request: JoinGameRequest,
    user: User = Depends(get_current_user),
    redis=Depends(get_redis_client),
):
    """Join an existing game."""
    try:
        game_id = request.game_id
        state_key = f"game:{game_id}:state"
        state_data = redis.get(state_key)

        if not state_data:
            raise HTTPException(status_code=404, detail="Game not found")

        state = json.loads(state_data)
        players: list[dict] = state.get("players", [])

        if any(player.get("id") == user.id for player in players):
            return {"status": "already_joined"}

        if len(players) >= 9:
            raise HTTPException(status_code=400, detail="Game is full")

        player_data = {
            "id": user.id,
            "name": user.username or f"User{user.id}",
            "chips": 1000,
            "status": "active",
            "cards": [],
            "current_bet": 0,
            "folded": False,
        }

        players.append(player_data)
        state["players"] = players

        redis.set(state_key, json.dumps(state), ex=7200)
        redis.set(f"game:{game_id}:player:{user.id}", json.dumps(player_data), ex=7200)

        return {"status": "joined", "player_count": len(players)}

    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/state/{game_id}")
async def get_game_state(
    game_id: str,
    user: User = Depends(get_current_user),
    redis=Depends(get_redis_client),
):
    """Retrieve the current state of a game."""
    try:
        state_key = f"game:{game_id}:state"
        state_data = redis.get(state_key)

        if not state_data:
            return {
                "game_id": game_id,
                "players": [],
                "pot": 0,
                "community_cards": [],
                "phase": "not_found",
                "current_turn": -1,
            }

        state = json.loads(state_data)

        if state.get("phase") not in {"finished", "showdown"}:
            for player in state.get("players", []):
                if player.get("id") != user.id:
                    player["cards"] = ["🂠", "🂠"]

        return state

    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/ready")
async def mark_ready(
    request: ReadyRequest,
    user: User = Depends(get_current_user),
    redis=Depends(get_redis_client),
):
    """Mark the current player as ready."""
    try:
        game_id = request.game_id
        state_key = f"game:{game_id}:state"
        state_data = redis.get(state_key)

        if not state_data:
            raise HTTPException(status_code=404, detail="Game not found")

        state = json.loads(state_data)
        ready_players: list[int] = state.setdefault("ready_players", [])

        if user.id not in ready_players:
            ready_players.append(user.id)

        players: list[dict] = state.get("players", [])
        player_count = len(players)
        ready_count = len(ready_players)

        if player_count >= 2 and ready_count == player_count:
            state["phase"] = "pre_flop"
            state["current_turn"] = (state.get("dealer_index", 0) + 3) % player_count
            state = deal_cards(state)

        redis.set(state_key, json.dumps(state), ex=7200)

        return {
            "status": "ready",
            "ready_count": ready_count,
            "total_players": player_count,
            "game_started": state.get("phase") != "waiting",
        }

    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/action")
async def game_action(
    request: GameActionRequest,
    user: User = Depends(get_current_user),
    redis=Depends(get_redis_client),
):
    """Perform a poker action."""
    try:
        game_id = request.game_id
        state_key = f"game:{game_id}:state"
        state_data = redis.get(state_key)

        if not state_data:
            raise HTTPException(status_code=404, detail="Game not found")

        state = json.loads(state_data)
        players: list[dict] = state.get("players", [])

        if not players:
            raise HTTPException(status_code=400, detail="Game has no players")

        current_turn = state.get("current_turn", -1)
        if current_turn < 0 or current_turn >= len(players):
            raise HTTPException(status_code=400, detail="Invalid turn state")

        current_player = players[current_turn]
        if current_player.get("id") != user.id:
            raise HTTPException(status_code=400, detail="Not your turn")

        try:
            state = process_action(state, request.action, request.amount)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        redis.set(state_key, json.dumps(state), ex=7200)

        return {"status": "action_processed", "next_turn": state.get("current_turn")}

    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/leave/{game_id}")
async def leave_game(
    game_id: str,
    user: User = Depends(get_current_user),
    redis=Depends(get_redis_client),
):
    """Allow a player to leave a game."""
    try:
        state_key = f"game:{game_id}:state"
        state_data = redis.get(state_key)

        if state_data:
            state = json.loads(state_data)
            state["players"] = [player for player in state.get("players", []) if player.get("id") != user.id]
            redis.set(state_key, json.dumps(state), ex=7200)
            redis.delete(f"game:{game_id}:player:{user.id}")

        return {"status": "left"}

    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc
