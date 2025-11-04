from fastapi import APIRouter, Depends, HTTPException
from typing import List
import logging

from ..dependencies import get_current_user, get_redis_client
from ..models import GameListResponse, GameStateResponse, GameActionRequest, JoinGameRequest
from redis import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/game", tags=["game"])


@router.get("/list", response_model=List[GameListResponse])
async def get_game_list(
    redis: Redis = Depends(get_redis_client),
    current_user: dict = Depends(get_current_user),
):
    """
    Get list of all active games.

    Returns:
        List of active game sessions with basic info
    """
    try:
        game_keys = redis.keys("game:*:state")

        games = []
        for key in game_keys:
            game_id = key.split(":")[1]

            game_data = redis.hgetall(key)
            if not game_data:
                continue

            player_keys = [name for name in game_data.keys() if name.startswith("player_")]

            game_info = {
                "game_id": game_id,
                "player_count": len(player_keys),
                "max_players": int(game_data.get("max_players", 6)),
                "small_blind": int(game_data.get("small_blind", 10)),
                "big_blind": int(game_data.get("big_blind", 20)),
                "status": game_data.get("status", "waiting"),
            }

            games.append(game_info)

        logger.info("User %s fetched %d games", current_user["user_id"], len(games))
        return games

    except Exception as e:
        logger.error("Error fetching game list: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch games") from e


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
        if not redis.exists(game_key):
            raise HTTPException(status_code=404, detail="Game not found")

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
