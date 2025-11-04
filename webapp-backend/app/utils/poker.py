import random
from typing import Dict, List, Any, Optional

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

def create_deck() -> List[str]:
    """Create shuffled deck"""
    deck = [f"{rank}{suit}" for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck

def deal_cards(state: Dict[str, Any]) -> Dict[str, Any]:
    """Deal hole cards and post blinds"""
    deck = create_deck()
    
    for player in state["players"]:
        player["cards"] = [deck.pop(), deck.pop()]
        player["current_bet"] = 0
        player["folded"] = False
    
    state["deck"] = deck
    state["community_cards"] = []
    
    # Post blinds
    player_count = len(state["players"])
    dealer_idx = state["dealer_index"]
    
    sb_idx = (dealer_idx + 1) % player_count
    bb_idx = (dealer_idx + 2) % player_count
    
    state["players"][sb_idx]["chips"] -= state["small_blind"]
    state["players"][sb_idx]["current_bet"] = state["small_blind"]
    state["pot"] += state["small_blind"]
    
    state["players"][bb_idx]["chips"] -= state["big_blind"]
    state["players"][bb_idx]["current_bet"] = state["big_blind"]
    state["pot"] += state["big_blind"]
    
    state["current_bet"] = state["big_blind"]
    state["last_raiser"] = bb_idx
    
    return state

def process_action(
    state: Dict[str, Any],
    action: str,
    amount: Optional[int] = None
) -> Dict[str, Any]:
    """Process player action"""
    current_idx = state["current_turn"]
    player = state["players"][current_idx]
    
    if action == "fold":
        player["folded"] = True
        player["status"] = "folded"
        
    elif action == "check":
        if player["current_bet"] < state.get("current_bet", 0):
            raise ValueError("Cannot check")
            
    elif action == "call":
        call_amt = state["current_bet"] - player["current_bet"]
        actual = min(call_amt, player["chips"])
        
        player["chips"] -= actual
        player["current_bet"] += actual
        state["pot"] += actual
        
    elif action == "raise":
        if not amount:
            raise ValueError("Raise amount required")
            
        call_amt = state["current_bet"] - player["current_bet"]
        total = call_amt + amount
        
        if player["chips"] < total:
            raise ValueError("Insufficient chips")
        
        player["chips"] -= total
        player["current_bet"] += total
        state["pot"] += total
        state["current_bet"] = player["current_bet"]
        state["last_raiser"] = current_idx
        
    elif action == "all_in":
        all_in_amt = player["chips"]
        player["chips"] = 0
        player["current_bet"] += all_in_amt
        state["pot"] += all_in_amt
        player["status"] = "all_in"
        
        if player["current_bet"] > state["current_bet"]:
            state["current_bet"] = player["current_bet"]
            state["last_raiser"] = current_idx
    
    state = advance_turn(state)
    return state

def advance_turn(state: Dict[str, Any]) -> Dict[str, Any]:
    """Move to next player"""
    player_count = len(state["players"])
    next_idx = (state["current_turn"] + 1) % player_count
    
    attempts = 0
    while state["players"][next_idx]["folded"] and attempts < player_count:
        next_idx = (next_idx + 1) % player_count
        attempts += 1
    
    state["current_turn"] = next_idx
    
    if check_round_complete(state):
        state = advance_street(state)
    
    return state

def check_round_complete(state: Dict[str, Any]) -> bool:
    """Check if betting round complete"""
    active = [p for p in state["players"] if not p["folded"]]
    
    if len(active) <= 1:
        return True
    
    current_bet = state.get("current_bet", 0)
    all_matched = all(
        p["current_bet"] == current_bet or p["chips"] == 0
        for p in active
    )
    
    return all_matched

def advance_street(state: Dict[str, Any]) -> Dict[str, Any]:
    """Move to next street"""
    for player in state["players"]:
        player["current_bet"] = 0
    
    state["current_bet"] = 0
    
    if state["phase"] == "pre_flop":
        state["community_cards"] = [
            state["deck"].pop(),
            state["deck"].pop(),
            state["deck"].pop()
        ]
        state["phase"] = "flop"
        
    elif state["phase"] == "flop":
        state["community_cards"].append(state["deck"].pop())
        state["phase"] = "turn"
        
    elif state["phase"] == "turn":
        state["community_cards"].append(state["deck"].pop())
        state["phase"] = "river"
        
    elif state["phase"] == "river":
        state["phase"] = "showdown"
        state = determine_winner(state)
    
    dealer_idx = state["dealer_index"]
    state["current_turn"] = (dealer_idx + 1) % len(state["players"])
    
    return state

def determine_winner(state: Dict[str, Any]) -> Dict[str, Any]:
    """Determine winner"""
    active = [p for p in state["players"] if not p["folded"]]
    
    if len(active) == 1:
        winner = active[0]
    else:
        winner = random.choice(active)
    
    winner["chips"] += state["pot"]
    state["winner_id"] = winner["id"]
    state["pot"] = 0
    state["phase"] = "finished"
    
    return state
