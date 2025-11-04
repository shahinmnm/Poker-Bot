import { GameState, GameListItem, AuthResponse, GameAction } from '../types/game.types';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'https://poker.shahin8n.sbs/api';

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function fetchApi<T>(
  endpoint: string,
  options: RequestInit = {},
  token?: string
): Promise<T> {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new ApiError(response.status, error.detail || 'Request failed');
  }

  return response.json();
}

// Auth endpoints
export const authenticateWithTelegram = async (initData: string): Promise<AuthResponse> => {
  return fetchApi<AuthResponse>('/auth/telegram', {
    method: 'POST',
    body: JSON.stringify({ initData }),
  });
};

// Game endpoints
export const getGameList = async (token: string): Promise<GameListItem[]> => {
  return fetchApi('/game/list', {}, token);
};

export const getGameState = async (gameId: string, token: string): Promise<GameState> => {
  return fetchApi(`/game/state/${gameId}`, {}, token);
};

export const joinGame = async (gameId: string, token: string): Promise<{ success: boolean }> => {
  return fetchApi(
    '/game/join',
    {
      method: 'POST',
      body: JSON.stringify({ game_id: gameId }),
    },
    token
  );
};

export const performAction = async (action: GameAction, token: string): Promise<{ success: boolean }> => {
  return fetchApi(
    '/game/action',
    {
      method: 'POST',
      body: JSON.stringify(action),
    },
    token
  );
};

export const leaveGame = async (gameId: string, token: string): Promise<{ success: boolean }> => {
  return fetchApi(
    '/game/leave',
    {
      method: 'POST',
      body: JSON.stringify({ game_id: gameId }),
    },
    token
  );
};

interface CreateGameResponse {
  game_id: string;
  status: string;
}

export const createGame = async (
  stakeLevel: string,
  token: string,
  mode: string = 'private'
): Promise<CreateGameResponse> => {
  return fetchApi(
    '/game/create',
    {
      method: 'POST',
      body: JSON.stringify({ stake_level: stakeLevel, mode }),
    },
    token
  );
};
