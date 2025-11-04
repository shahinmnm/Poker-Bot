from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from pydantic import BaseModel
import json
import uuid
from datetime import datetime

from app.dependencies import get_current_user, get_redis_client
from app.models import User


STAKE_LEVEL_PRESETS = {
    "micro": {"stake": "5/10", "small_blind": 5, "big_blind": 10},
    "low": {"stake": "10/20", "small_blind": 10, "big_blind": 20},
    "medium": {"stake": "25/50", "small_blind": 25, "big_blind": 50},
    "high": {"stake": "50/100", "small_blind": 50, "big_blind": 100},
    "premium": {"stake": "100/200", "small_blind": 100, "big_blind": 200},
}

DEFAULT_STAKE_LEVEL = "micro"

router = APIRouter(tags=["game"])


# Request models
class CreateGameRequest(BaseModel):
    stake: Optional[str] = None
    stake_level: Optional[str] = None
    mode: str = "group"
    buy_in: Optional[int] = None
    

class JoinGameRequest(BaseModel):
    game_id: str
    

class GameActionRequest(BaseModel):
    game_id: str
    action: str
    amount: Optional[int] = None


class ReadyRequest(BaseModel):
    game_id: str


@router.get("/list")
async def list_games(
    user: User = Depends(get_current_user),
    redis = Depends(get_redis_client)
):
    """List all active games"""
    try:
        games = []
        
        # Scan for game keys
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match="game:*:meta", count=100)
            
            for key in keys:
                game_id = key.decode().split(":")[1]
                meta_data = await redis.get(key)
                
                if meta_data:
                    meta = json.loads(meta_data)
                    
                    # Get player count
                    state_key = f"game:{game_id}:state"
                    state_data = await redis.get(state_key)
                    
                    if state_data:
                        state = json.loads(state_data)
                        player_count = len(state.get("players", []))
                    else:
                        player_count = 0
                    
                    games.append({
                        "id": game_id,
                        "stake": meta.get("stake", "1/2"),
                        "player_count": player_count,
                        "mode": meta.get("mode", "group"),
                        "status": meta.get("status", "waiting"),
                        "min_players": 2,
                        "max_players": 9
                    })
            
            if cursor == 0:
                break
        
        return {"games": games}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create")
async def create_game(
    request: CreateGameRequest,
    user: User = Depends(get_current_user),
    redis = Depends(get_redis_client)
):
    """Create a new game"""
    try:
        game_id = str(uuid.uuid4())

        stake_level = (request.stake_level or "").lower() or None
        stake_value = request.stake

        if stake_level:
            preset = STAKE_LEVEL_PRESETS.get(stake_level)
            if not preset:
                raise HTTPException(status_code=400, detail="Invalid stake level selected")

            stake_value = preset["stake"]
            small_blind = preset["small_blind"]
            big_blind = preset["big_blind"]
        else:
            if not stake_value:
                preset = STAKE_LEVEL_PRESETS[DEFAULT_STAKE_LEVEL]
                stake_level = DEFAULT_STAKE_LEVEL
                stake_value = preset["stake"]
                small_blind = preset["small_blind"]
                big_blind = preset["big_blind"]
            else:
                try:
                    small_str, big_str = [part.strip() for part in stake_value.split("/")]
                    small_blind = int(small_str)
                    big_blind = int(big_str)
                except (ValueError, AttributeError):
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid stake format. Expected 'small/big'."
                    )

        # Store metadata
        meta = {
            "stake": stake_value,
            "mode": request.mode,
            "status": "waiting",
            "creator_id": user.id,
            "created_at": datetime.now().isoformat(),
            "stake_level": stake_level
        }
        
        await redis.set(
            f"game:{game_id}:meta",
            json.dumps(meta),
            ex=7200
        )
        
        # Initialize state
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
            "current_bet": 0
        }
        
        await redis.set(
            f"game:{game_id}:state",
            json.dumps(initial_state),
            ex=7200
        )
        
        return {"game_id": game_id, "status": "created"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/join")
async def join_game(
    request: JoinGameRequest,
    user: User = Depends(get_current_user),
    redis = Depends(get_redis_client)
):
    """Join a game"""
    try:
        game_id = request.game_id
        state_key = f"game:{game_id}:state"
        state_data = await redis.get(state_key)
        
        if not state_data:
            raise HTTPException(status_code=404, detail="Game not found")
        
        state = json.loads(state_data)
        
        # Check if already joined
        if any(p["id"] == user.id for p in state["players"]):
            return {"status": "already_joined"}
        
        # Check max players
        if len(state["players"]) >= 9:
            raise HTTPException(status_code=400, detail="Game is full")
        
        # Add player
        player_data = {
            "id": user.id,
            "name": user.username or f"Player{user.id}",
            "chips": 1000,
            "status": "active",
            "cards": [],
            "current_bet": 0,
            "folded": False
        }
        
        state["players"].append(player_data)
        await redis.set(state_key, json.dumps(state), ex=7200)
        
        return {"status": "joined", "player_count": len(state["players"])}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/state/{game_id}")
async def get_game_state(
    game_id: str,
    user: User = Depends(get_current_user),
    redis = Depends(get_redis_client)
):
    """Get game state"""
    try:
        state_key = f"game:{game_id}:state"
        state_data = await redis.get(state_key)
        
        if not state_data:
            return {
                "game_id": game_id,
                "players": [],
                "pot": 0,
                "community_cards": [],
                "phase": "not_found",
                "current_turn": -1
            }
        
        state = json.loads(state_data)
        
        # Hide other players' cards
        if state["phase"] not in ["finished", "showdown"]:
            for player in state["players"]:
                if player["id"] != user.id:
                    player["cards"] = ["🂠", "🂠"]
        
        return state
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ready")
async def mark_ready(
    request: ReadyRequest,
    user: User = Depends(get_current_user),
    redis = Depends(get_redis_client)
):
    """Mark ready"""
    try:
        state_key = f"game:{request.game_id}:state"
        state_data = await redis.get(state_key)
        
        if not state_data:
            raise HTTPException(status_code=404, detail="Game not found")
        
        state = json.loads(state_data)
        
        if user.id not in state["ready_players"]:
            state["ready_players"].append(user.id)
        
        player_count = len(state["players"])
        ready_count = len(state["ready_players"])
        
        # Start game if all ready
        if player_count >= 2 and ready_count == player_count:
            state["phase"] = "pre_flop"
            state["current_turn"] = (state["dealer_index"] + 3) % player_count
            
            # Deal cards
            from app.utils.poker import deal_cards
            state = deal_cards(state)
        
        await redis.set(state_key, json.dumps(state), ex=7200)
        
        return {
            "status": "ready",
            "ready_count": ready_count,
            "total_players": player_count,
            "game_started": state["phase"] != "waiting"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/action")
async def game_action(
    request: GameActionRequest,
    user: User = Depends(get_current_user),
    redis = Depends(get_redis_client)
):
    """Perform action"""
    try:
        state_key = f"game:{request.game_id}:state"
        state_data = await redis.get(state_key)
        
        if not state_data:
            raise HTTPException(status_code=404, detail="Game not found")
        
        state = json.loads(state_data)
        
        # Validate turn
        current_player = state["players"][state["current_turn"]]
        if current_player["id"] != user.id:
            raise HTTPException(status_code=400, detail="Not your turn")
        
        # Process action
        from app.utils.poker import process_action
        state = process_action(state, request.action, request.amount)
        
        await redis.set(state_key, json.dumps(state), ex=7200)
        
        return {"status": "action_processed", "next_turn": state["current_turn"]}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/leave/{game_id}")
async def leave_game(
    game_id: str,
    user: User = Depends(get_current_user),
    redis = Depends(get_redis_client)
):
    """Leave game"""
    try:
        state_key = f"game:{game_id}:state"
        state_data = await redis.get(state_key)
        
        if state_data:
            state = json.loads(state_data)
            state["players"] = [p for p in state["players"] if p["id"] != user.id]
            await redis.set(state_key, json.dumps(state), ex=7200)
        
        return {"status": "left"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
