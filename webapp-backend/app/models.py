"""Pydantic models used by the FastAPI routes."""

from typing import List, Optional

from pydantic import BaseModel


class User(BaseModel):
    id: int
    username: Optional[str] = None
    telegram_id: Optional[int] = None


class TelegramAuthRequest(BaseModel):
    init_data: str


class TokenResponse(BaseModel):
    token: str
    user_id: int
    username: str


class GameListResponse(BaseModel):
    game_id: str
    player_count: int
    max_players: int
    small_blind: int
    big_blind: int
    status: str
    mode: str
    pot: int
    stake_level: Optional[str] = None
    created_at: Optional[str] = None
    chat_id: Optional[str] = None
    host: Optional[str] = None


class PlayerInfo(BaseModel):
    user_id: int
    username: str
    chips: int
    is_active: bool


class GameStateResponse(BaseModel):
    game_id: str
    status: str
    players: List[PlayerInfo]
    current_bet: int
    pot: int
    community_cards: List[str]
    your_cards: List[str]
    current_turn_user_id: Optional[int]


class JoinGameRequest(BaseModel):
    game_id: str


class GameActionRequest(BaseModel):
    game_id: str
    action: str  # "fold", "call", "raise", "check"
    amount: Optional[int] = None
