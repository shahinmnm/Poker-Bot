from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


def create_deck() -> List[str]:
    """Create a shuffled deck of cards."""
    deck = [f"{rank}{suit}" for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck


def deal_cards(state: Dict[str, Any]) -> Dict[str, Any]:
    """Deal two hole cards to each player and post blinds."""
    players = state.get("players", [])
    if not players:
        return state

    deck = create_deck()

    for player in players:
        player["cards"] = [deck.pop(), deck.pop()]
        player["current_bet"] = 0
        player["folded"] = False
        player.setdefault("status", "active")

    state["deck"] = deck
    state["community_cards"] = []
    state.setdefault("pot", 0)

    player_count = len(players)
    dealer_idx = state.get("dealer_index", 0) % player_count

    sb_idx = (dealer_idx + 1) % player_count
    bb_idx = (dealer_idx + 2) % player_count

    small_blind = int(state.get("small_blind", 0))
    big_blind = int(state.get("big_blind", 0))

    players[sb_idx]["chips"] -= small_blind
    players[sb_idx]["current_bet"] = small_blind
    state["pot"] += small_blind

    players[bb_idx]["chips"] -= big_blind
    players[bb_idx]["current_bet"] = big_blind
    state["pot"] += big_blind

    state["current_bet"] = big_blind
    state["last_raiser"] = bb_idx

    return state


def process_action(state: Dict[str, Any], action: str, amount: Optional[int] = None) -> Dict[str, Any]:
    """Process a player action such as fold, call, raise, etc."""
    players = state.get("players", [])
    if not players:
        raise ValueError("No players in game")

    current_idx = state.get("current_turn", -1)
    if current_idx < 0 or current_idx >= len(players):
        raise ValueError("Invalid turn index")

    player = players[current_idx]

    if action == "fold":
        player["folded"] = True
        player["status"] = "folded"

    elif action == "check":
        if player.get("current_bet", 0) < state.get("current_bet", 0):
            raise ValueError("Cannot check - must call or raise")

    elif action == "call":
        call_amount = state.get("current_bet", 0) - player.get("current_bet", 0)
        actual_call = min(call_amount, player.get("chips", 0))

        player["chips"] -= actual_call
        player["current_bet"] = player.get("current_bet", 0) + actual_call
        state["pot"] = state.get("pot", 0) + actual_call

    elif action == "raise":
        if amount is None:
            raise ValueError("Raise amount required")

        call_amount = state.get("current_bet", 0) - player.get("current_bet", 0)
        total_bet = call_amount + amount

        if player.get("chips", 0) < total_bet:
            raise ValueError("Insufficient chips")

        player["chips"] -= total_bet
        player["current_bet"] = player.get("current_bet", 0) + total_bet
        state["pot"] = state.get("pot", 0) + total_bet
        state["current_bet"] = player["current_bet"]
        state["last_raiser"] = current_idx

    elif action == "all_in":
        all_in_amount = player.get("chips", 0)
        player["chips"] = 0
        player["current_bet"] = player.get("current_bet", 0) + all_in_amount
        state["pot"] = state.get("pot", 0) + all_in_amount
        player["status"] = "all_in"

        if player["current_bet"] > state.get("current_bet", 0):
            state["current_bet"] = player["current_bet"]
            state["last_raiser"] = current_idx

    else:
        raise ValueError("Unknown action")

    state = advance_turn(state)
    return state


def advance_turn(state: Dict[str, Any]) -> Dict[str, Any]:
    """Advance the action to the next player."""
    players = state.get("players", [])
    player_count = len(players)

    if player_count == 0:
        state["current_turn"] = -1
        return state

    next_idx = (state.get("current_turn", -1) + 1) % player_count

    attempts = 0
    while players[next_idx].get("folded") and attempts < player_count:
        next_idx = (next_idx + 1) % player_count
        attempts += 1

    state["current_turn"] = next_idx

    if check_round_complete(state):
        state = advance_street(state)

    return state


def check_round_complete(state: Dict[str, Any]) -> bool:
    """Determine if the betting round has finished."""
    players = state.get("players", [])
    active_players = [player for player in players if not player.get("folded")]

    if len(active_players) <= 1:
        return True

    current_bet = state.get("current_bet", 0)
    last_raiser = state.get("last_raiser", -1)

    all_matched = all(
        player.get("current_bet", 0) == current_bet or player.get("chips", 0) == 0
        for player in active_players
    )

    return all_matched and state.get("current_turn") == last_raiser


def advance_street(state: Dict[str, Any]) -> Dict[str, Any]:
    """Advance the game to the next betting street."""
    players = state.get("players", [])

    for player in players:
        player["current_bet"] = 0

    state["current_bet"] = 0

    if state.get("phase") == "pre_flop":
        state["community_cards"] = [state["deck"].pop() for _ in range(3)]
        state["phase"] = "flop"

    elif state.get("phase") == "flop":
        state["community_cards"].append(state["deck"].pop())
        state["phase"] = "turn"

    elif state.get("phase") == "turn":
        state["community_cards"].append(state["deck"].pop())
        state["phase"] = "river"

    elif state.get("phase") == "river":
        state["phase"] = "showdown"
        state = determine_winner(state)

    dealer_idx = state.get("dealer_index", 0)
    player_count = len(players)
    if player_count > 0:
        state["current_turn"] = (dealer_idx + 1) % player_count
    else:
        state["current_turn"] = -1

    return state


def determine_winner(state: Dict[str, Any]) -> Dict[str, Any]:
    """Pick a winner and distribute the pot. Simplified for now."""
    active_players = [player for player in state.get("players", []) if not player.get("folded")]

    if len(active_players) == 1:
        winner = active_players[0]
    elif active_players:
        winner = random.choice(active_players)
    else:
        state["winner_id"] = None
        state["pot"] = 0
        state["phase"] = "finished"
        return state

    winner["chips"] = winner.get("chips", 0) + state.get("pot", 0)
    state["winner_id"] = winner.get("id")
    state["pot"] = 0
    state["phase"] = "finished"

    return state
