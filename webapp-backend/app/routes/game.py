from typing import List

import datetime
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..dependencies import get_current_user, get_redis_client
from ..models import (
    GameListResponse,
    GameStateResponse,
    GameActionRequest,
    JoinGameRequest,
)
from redis import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/game", tags=["game"])

STAKE_LEVEL_BLINDS = {
    "micro": (5, 10),
    "low": (10, 20),
    "medium": (25, 50),
    "high": (50, 100),
    "premium": (100, 200),
}


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class CreateGameRequest(BaseModel):
    stake_level: str
    mode: str = "private"


class CreateGameResponse(BaseModel):
    game_id: str
    status: str


@router.get("/list", response_model=List[GameListResponse])
async def get_game_list(
    redis: Redis = Depends(get_redis_client),
    current_user: dict = Depends(get_current_user),
):
    """Return a list of active games visible to the web frontend."""

    try:
        games: List[dict] = []
        seen_ids: set[str] = set()

        meta_keys = redis.keys("game:*:meta")
        for key in meta_keys:
            raw_meta = redis.get(key)
            if not raw_meta:
                continue

            try:
                meta = json.loads(raw_meta)
            except json.JSONDecodeError:
                logger.warning("Invalid game metadata payload at %s", key)
                continue

            game_id = str(meta.get("game_id") or key.split(":")[1])
            seen_ids.add(game_id)

            try:
                pot_value = int(float(meta.get("pot", 0)))
            except (TypeError, ValueError):
                pot_value = 0

            games.append(
                {
                    "game_id": game_id,
                    "player_count": _safe_int(meta.get("players_count", 0)),
                    "max_players": _safe_int(meta.get("max_players", 8), 8),
                    "small_blind": _safe_int(meta.get("small_blind", 10), 10),
                    "big_blind": _safe_int(meta.get("big_blind", 20), 20),
                    "status": meta.get("state", "UNKNOWN"),
                    "mode": meta.get("mode", "unknown"),
                    "stake_level": meta.get("stake_level"),
                    "created_at": meta.get("created_at"),
                    "chat_id": meta.get("chat_id"),
                    "pot": pot_value,
                    "host": meta.get("host"),
                }
            )

        if not games:
            # Backwards compatibility – fall back to legacy state hashes
            state_keys = redis.keys("game:*:state")
            for key in state_keys:
                game_id = key.split(":")[1]
                if game_id in seen_ids:
                    continue

                game_data = redis.hgetall(key)
                if not game_data:
                    continue

                player_keys = [
                    name for name in game_data.keys() if name.startswith("player_")
                ]

                games.append(
                    {
                        "game_id": game_id,
                        "player_count": len(player_keys),
                        "max_players": _safe_int(game_data.get("max_players", 6), 6),
                        "small_blind": _safe_int(game_data.get("small_blind", 10), 10),
                        "big_blind": _safe_int(game_data.get("big_blind", 20), 20),
                        "status": game_data.get("status", "waiting"),
                        "mode": "unknown",
                        "stake_level": None,
                        "created_at": None,
                        "chat_id": None,
                        "pot": _safe_int(game_data.get("pot", 0), 0),
                        "host": None,
                    }
                )

        logger.info("User %s fetched %d games", current_user["user_id"], len(games))
        return games

    except Exception as e:
        logger.error("Error fetching game list: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch games") from e


@router.post("/create", response_model=CreateGameResponse)
async def create_game(
    request: CreateGameRequest,
    redis: Redis = Depends(get_redis_client),
    current_user: dict = Depends(get_current_user),
):
    """Create a standalone game that can be joined from the webapp."""

    try:
        game_id = str(uuid.uuid4())
        key = f"game:{game_id}:meta"

        small_blind, big_blind = STAKE_LEVEL_BLINDS.get(
            request.stake_level, STAKE_LEVEL_BLINDS["low"]
        )

        payload = {
            "game_id": game_id,
            "host": str(current_user.get("user_id")),
            "mode": request.mode,
            "stake_level": request.stake_level,
            "state": "INITIAL",
            "players_count": 1,
            "max_players": 8,
            "small_blind": small_blind,
            "big_blind": big_blind,
            "pot": 0,
            "chat_id": "webapp",
            "created_at": datetime.datetime.utcnow().isoformat(),
        }

        redis.set(key, json.dumps(payload), ex=86400)
        logger.info(
            "User %s created webapp game %s with stake %s",
            current_user.get("user_id"),
            game_id,
            request.stake_level,
        )
        return {"game_id": game_id, "status": "created"}

    except Exception as e:
        logger.error("Error creating game: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create game") from e


@router.post("/join")
async def join_game(
    request: JoinGameRequest,
    redis: Redis = Depends(get_redis_client),
    current_user: dict = Depends(get_current_user),
):
    """
    Join an existing game or create a new one.

    Args:
        request: Join game request with game_id

    Returns:
        Game state after joining
    """
    try:
        game_id = request.game_id
        user_id = current_user["user_id"]

        game_key = f"game:{game_id}:state"
        meta_key = f"game:{game_id}:meta"

        if not redis.exists(game_key):
            meta_payload = redis.get(meta_key)
            if not meta_payload:
                raise HTTPException(status_code=404, detail="Game not found")

            try:
                meta = json.loads(meta_payload)
            except json.JSONDecodeError as exc:
                logger.error("Corrupt metadata for game %s: %s", game_id, exc)
                raise HTTPException(status_code=500, detail="Game metadata invalid") from exc

            meta["players_count"] = _safe_int(meta.get("players_count", 0)) + 1
            joined = meta.get("joined_players")
            if not isinstance(joined, list):
                joined = []
            if str(user_id) not in joined:
                joined.append(str(user_id))
            meta["joined_players"] = joined
            meta["updated_at"] = datetime.datetime.utcnow().isoformat()
            redis.set(meta_key, json.dumps(meta), ex=86400)

            logger.info("User %s joined webapp game %s", user_id, game_id)

            return {
                "game_id": game_id,
                "status": "joined",
                "player_count": meta["players_count"],
            }

        player_key = f"player_{user_id}"
        redis.hset(game_key, player_key, current_user.get("username", ""))

        game_data = redis.hgetall(game_key)

        logger.info("User %s joined game %s", user_id, game_id)

        return {
            "game_id": game_id,
            "status": "joined",
            "player_count": len([k for k in game_data.keys() if k.startswith("player_")]),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error joining game: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to join game") from e


@router.get("/state/{game_id}", response_model=GameStateResponse)
async def get_game_state(
    game_id: str,
    redis: Redis = Depends(get_redis_client),
    current_user: dict = Depends(get_current_user),
):
    """
    Get current state of a game.

    Args:
        game_id: Game identifier

    Returns:
        Current game state
    """
    try:
        game_key = f"game:{game_id}:state"

        if not redis.exists(game_key):
            raise HTTPException(status_code=404, detail="Game not found")

        game_data = redis.hgetall(game_key)

        players = []
        for key, value in game_data.items():
            if key.startswith("player_"):
                user_id = int(key.replace("player_", ""))
                chips_key = f"chips_{user_id}"
                players.append({
                    "user_id": user_id,
                    "username": value,
                    "chips": int(game_data.get(chips_key, 1000)),
                    "is_active": True,
                })

        response = {
            "game_id": game_id,
            "status": game_data.get("status", "waiting"),
            "players": players,
            "current_bet": int(game_data.get("current_bet", 0)),
            "pot": int(game_data.get("pot", 0)),
            "community_cards": [],
            "your_cards": [],
            "current_turn_user_id": None,
        }

        logger.info("User %s fetched state for game %s", current_user["user_id"], game_id)
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching game state: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch game state") from e


@router.post("/action")
async def perform_action(
    request: GameActionRequest,
    redis: Redis = Depends(get_redis_client),
    current_user: dict = Depends(get_current_user),
):
    """
    Perform a game action (fold, call, raise, check).

    Args:
        request: Action request

    Returns:
        Updated game state
    """
    try:
        game_id = request.game_id
        action = request.action
        amount = request.amount

        logger.info(
            "User %s performing %s in game %s with amount %s",
            current_user["user_id"],
            action,
            game_id,
            amount,
        )

        return {
            "success": True,
            "action": action,
            "game_id": game_id,
        }

    except Exception as e:
        logger.error("Error performing action: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to perform action") from e


@router.post("/leave/{game_id}")
async def leave_game(
    game_id: str,
    redis: Redis = Depends(get_redis_client),
    current_user: dict = Depends(get_current_user),
):
    """
    Leave a game.

    Args:
        game_id: Game identifier

    Returns:
        Success status
    """
    try:
        user_id = current_user["user_id"]
        game_key = f"game:{game_id}:state"

        player_key = f"player_{user_id}"
        redis.hdel(game_key, player_key)

        logger.info("User %s left game %s", user_id, game_id)

        return {"success": True, "game_id": game_id}

    except Exception as e:
        logger.error("Error leaving game: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to leave game") from e
