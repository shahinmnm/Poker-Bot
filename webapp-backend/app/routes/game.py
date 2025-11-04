from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import List
import json
import uuid
from datetime import datetime

from app.dependencies import get_current_user, get_redis

router = APIRouter()


class GameStateResponse(BaseModel):
    game_id: str
    players: List[dict]
    pot: int
    community_cards: List[str]
    current_turn: int
    phase: str


class CreateGameRequest(BaseModel):
    stake: str = "1/2"


class JoinGameRequest(BaseModel):
    game_id: str


@router.get("/list")
async def list_games(
    request: Request,
    user_id: int = Depends(get_current_user),
):
    """List all active games - NO AUTH REQUIRED in permissive mode."""
    redis_client = get_redis()

    games = []
    for key in redis_client.scan_iter("game:*:meta"):
        try:
            game_data = redis_client.get(key)
            if game_data:
                game = json.loads(game_data)
                games.append(
                    {
                        "id": game.get("game_id"),
                        "stake": game.get("stake", "1/2"),
                        "player_count": len(game.get("players", [])),
                    }
                )
        except Exception as e:
            print(f"Error loading game {key}: {e}")
            continue

    return {"games": games, "user_id": user_id}


@router.get("/state/{game_id}")
async def get_game_state(
    game_id: str,
    request: Request,
    user_id: int = Depends(get_current_user),
) -> GameStateResponse:
    """Get current state of a game."""
    redis_client = get_redis()

    # Get game metadata
    meta_key = f"game:{game_id}:meta"
    meta_data = redis_client.get(meta_key)

    if not meta_data:
        raise HTTPException(status_code=404, detail="Game not found")

    meta = json.loads(meta_data)

    # Get game state if available
    state_key = f"game:{game_id}:state"
    state_data = redis_client.get(state_key)

    if state_data:
        state = json.loads(state_data)
        return GameStateResponse(**state)

    # Return minimal state for new games
    return GameStateResponse(
        game_id=game_id,
        players=[{"id": user_id, "name": f"Player{user_id}", "chips": 1000}],
        pot=0,
        community_cards=[],
        current_turn=user_id,
        phase="waiting",
    )


@router.post("/join")
async def join_game(
    request_body: JoinGameRequest,
    request: Request,
    user_id: int = Depends(get_current_user),
):
    """Join an existing game."""
    game_id = request_body.game_id
    redis_client = get_redis()

    meta_key = f"game:{game_id}:meta"
    meta_data = redis_client.get(meta_key)

    if not meta_data:
        raise HTTPException(status_code=404, detail="Game not found")

    meta = json.loads(meta_data)

    # Add user to players list
    if "players" not in meta:
        meta["players"] = []

    if user_id not in [p.get("id") for p in meta["players"]]:
        meta["players"].append({"id": user_id, "name": f"Player{user_id}"})
        redis_client.set(meta_key, json.dumps(meta))

    return {"success": True, "game_id": game_id}


@router.post("/create")
async def create_game(
    request_body: CreateGameRequest,
    request: Request,
    user_id: int = Depends(get_current_user),
):
    """Create a new game."""
    redis_client = get_redis()

    game_id = str(uuid.uuid4())
    meta = {
        "game_id": game_id,
        "stake": request_body.stake,
        "created_by": user_id,
        "created_at": datetime.now().isoformat(),
        "players": [{"id": user_id, "name": f"Player{user_id}"}],
    }

    meta_key = f"game:{game_id}:meta"
    redis_client.setex(meta_key, 3600, json.dumps(meta))

    return {"success": True, "game_id": game_id}
