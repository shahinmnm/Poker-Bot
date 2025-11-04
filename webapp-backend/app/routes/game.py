#!/usr/bin/env python3
"""
Game routes for WebApp - bridges with core poker engine.
"""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Depends, Header, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import redis.asyncio as aioredis

# Import core game logic from main bot
from pokerapp.game_engine import PokerEngine
from pokerapp.game_coordinator import GameCoordinator
from pokerapp.entities import Game, Player, GameState, PlayerState
from pokerapp.kvstore import ensure_kv
from pokerapp.winnerdetermination import WinnerDetermination

from app.utils.telegram import verify_session_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/game", tags=["game"])


def get_authenticated_user(
    authorization: Optional[str] = Header(default=None, convert_underscores=False)
):
    """Extract and validate the bearer token from the Authorization header."""

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    session = verify_session_token(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return session

# Redis connection (shared with main bot)
redis_client = aioredis.from_url(
    "redis://redis:6379",
    encoding="utf-8",
    decode_responses=True
)

# Initialize core game components
engine = PokerEngine()
coordinator = GameCoordinator()
winner_determine = WinnerDetermination()

# ==================== Request/Response Models ====================

class GameStateResponse(BaseModel):
    """Current game state snapshot."""
    game_id: str
    state: str  # GameState enum value
    pot: int
    current_bet: int
    players: List[Dict]
    community_cards: List[str]
    current_player_index: Optional[int]
    dealer_index: int

class PlayerActionRequest(BaseModel):
    """Player action (fold, call, raise, all-in)."""
    action: str  # "fold", "call", "raise", "all_in"
    amount: Optional[int] = None  # For raise action

class JoinGameRequest(BaseModel):
    """Request to join a game."""
    game_id: str
    stake: int  # 5, 10, 25, etc.

# ==================== Utility Functions ====================

async def get_game_from_redis(game_id: str) -> Optional[Game]:
    """Fetch game state from Redis."""
    try:
        kv = ensure_kv(redis_client)
        game_data = await kv.get(f"game:{game_id}")
        if not game_data:
            return None
        
        # Deserialize game object (implementation depends on your serialization)
        # For now, return a mock Game object
        game = Game()
        # TODO: Properly deserialize from Redis
        return game
    except Exception as e:
        logger.error(f"Failed to fetch game {game_id}: {e}")
        return None

async def save_game_to_redis(game_id: str, game: Game) -> bool:
    """Save game state to Redis."""
    try:
        kv = ensure_kv(redis_client)
        # TODO: Properly serialize game to Redis
        await kv.set(f"game:{game_id}", {"state": "serialized"}, ex=3600)
        return True
    except Exception as e:
        logger.error(f"Failed to save game {game_id}: {e}")
        return False

def serialize_game_state(game: Game) -> Dict:
    """Convert Game object to API response format."""
    return {
        "game_id": getattr(game, "id", "unknown"),
        "state": game.state.name if hasattr(game.state, 'name') else str(game.state),
        "pot": game.pot,
        "current_bet": game.current_bet,
        "players": [
            {
                "user_id": str(p.user_id),
                "mention": p.mention_markdown,
                "balance": p.wallet.value() if hasattr(p, 'wallet') else 0,
                "state": p.state.name if hasattr(p.state, 'name') else str(p.state),
                "cards": p.cards if hasattr(p, 'cards') else [],
                "round_rate": getattr(p, 'round_rate', 0)
            }
            for p in game.players
        ],
        "community_cards": getattr(game, 'community_cards', []),
        "current_player_index": getattr(game, 'current_player_index', None),
        "dealer_index": getattr(game, 'dealer_index', 0)
    }

# ==================== API Endpoints ====================

@router.get("/state/{game_id}")
async def get_game_state(
    game_id: str,
    session: Dict[str, Any] = Depends(get_authenticated_user)
) -> GameStateResponse:
    """
    Get current game state.
    
    Requires valid session token from Telegram auth.
    """
    game = await get_game_from_redis(game_id)

    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    # Verify user is part of this game
    player_ids = [p.user_id for p in game.players]
    if str(session["user_id"]) not in player_ids:
        raise HTTPException(status_code=403, detail="Not a player in this game")
    
    return serialize_game_state(game)

@router.post("/join")
async def join_game(
    request: JoinGameRequest,
    session: Dict[str, Any] = Depends(get_authenticated_user)
) -> Dict:
    """
    Join an existing game or create new one.
    
    Validates player balance against stake requirement.
    """
    # Fetch player wallet balance from Redis
    kv = ensure_kv(redis_client)
    user_id = session["user_id"]
    balance_key = f"wallet:{user_id}"
    balance = await kv.get(balance_key) or 0
    
    # Use engine to validate balance
    if not engine.validate_join_balance(balance, request.stake):
        big_blind = request.stake * 2
        min_required = big_blind * 20
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient balance. Need at least {min_required} (20 BB)"
        )
    
    game = await get_game_from_redis(request.game_id)
    
    if not game:
        # Create new game
        game = Game()
        game.id = request.game_id
        game.state = GameState.WAITING
        game.players = []
    
    # Add player to game (simplified - needs proper Player object)
    # TODO: Create proper Player with Wallet
    
    await save_game_to_redis(request.game_id, game)
    
    return {
        "success": True,
        "game_id": request.game_id,
        "message": "Joined game successfully"
    }

@router.post("/action/{game_id}")
async def player_action(
    game_id: str,
    action: PlayerActionRequest,
    session: Dict[str, Any] = Depends(get_authenticated_user)
) -> Dict:
    """
    Execute player action (fold, call, raise, all-in).
    
    Uses GameCoordinator to process action through core engine.
    """
    game = await get_game_from_redis(game_id)
    
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    if game.state not in [GameState.PREFLOP, GameState.FLOP, GameState.TURN, GameState.RIVER]:
        raise HTTPException(status_code=400, detail="Game not in active betting round")
    
    # Find player
    user_id = session["user_id"]
    player = next((p for p in game.players if str(p.user_id) == str(user_id)), None)
    
    if not player:
        raise HTTPException(status_code=403, detail="Not a player in this game")
    
    if game.current_player_index != game.players.index(player):
        raise HTTPException(status_code=400, detail="Not your turn")
    
    # Process action through coordinator
    try:
        if action.action == "fold":
            result = await coordinator.process_fold(game, player)
        elif action.action == "call":
            result = await coordinator.process_call(game, player)
        elif action.action == "raise":
            if not action.amount:
                raise HTTPException(status_code=400, detail="Raise amount required")
            result = await coordinator.process_raise(game, player, action.amount)
        elif action.action == "all_in":
            result = await coordinator.process_all_in(game, player)
        else:
            raise HTTPException(status_code=400, detail=f"Invalid action: {action.action}")
        
        await save_game_to_redis(game_id, game)
        
        return {
            "success": True,
            "action": action.action,
            "result": result.value if hasattr(result, 'value') else str(result),
            "game_state": serialize_game_state(game)
        }
    
    except Exception as e:
        logger.error(f"Action processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.websocket("/ws/{game_id}")
async def game_websocket(websocket: WebSocket, game_id: str):
    """
    WebSocket for real-time game updates.
    
    Broadcasts state changes to all connected players.
    """
    await websocket.accept()
    
    try:
        while True:
            # Wait for messages from client
            data = await websocket.receive_json()
            
            # Fetch current game state
            game = await get_game_from_redis(game_id)
            
            if game:
                # Send updated state
                await websocket.send_json(serialize_game_state(game))
            else:
                await websocket.send_json({"error": "Game not found"})
    
    except WebSocketDisconnect:
        logger.info(f"Client disconnected from game {game_id}")
