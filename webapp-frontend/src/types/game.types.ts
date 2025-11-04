export interface Player {
  user_id: number;
  username: string;
  stack: number;
  bet: number;
  is_active: boolean;
  is_current: boolean;
  cards: string[];
}

export interface GameState {
  game_id: string;
  state: 'INITIAL' | 'PRE_FLOP' | 'FLOP' | 'TURN' | 'RIVER' | 'FINISHED';
  pot: number;
  current_bet: number;
  community_cards: string[];
  players: Player[];
  your_cards: string[];
  your_turn: boolean;
  available_actions: string[];
}

export interface GameListItem {
  game_id: string;
  player_count: number;
  max_players: number;
  small_blind: number;
  big_blind: number;
  status: string;
  mode: string;
  pot: number;
  stake_level?: string;
  created_at?: string;
  chat_id?: string;
  host?: string;
}

export interface AuthResponse {
  success: boolean;
  session_token: string;
  user_id: number;
  username?: string;
  expires_at: string;
}

export type ActionType = 'fold' | 'check' | 'call' | 'raise' | 'bet' | 'all_in';

export interface GameAction {
  game_id: string;
  action: ActionType;
  amount?: number;
}
