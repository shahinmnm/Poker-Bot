import { useState, useEffect, useCallback } from 'react';
import { GameState, GameAction } from '../types/game.types';
import { getGameState, performAction } from '../services/api';

export const useGame = (gameId: string | null, sessionToken: string | null) => {
  const [gameState, setGameState] = useState<GameState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchGameState = useCallback(async () => {
    if (!gameId || !sessionToken) return;

    try {
      setLoading(true);
      const state = await getGameState(gameId, sessionToken);
      setGameState(state);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch game state');
    } finally {
      setLoading(false);
    }
  }, [gameId, sessionToken]);

  useEffect(() => {
    fetchGameState();

    // Poll for updates every 2 seconds
    const interval = setInterval(fetchGameState, 2000);
    return () => clearInterval(interval);
  }, [fetchGameState]);

  const executeAction = async (action: GameAction) => {
    if (!sessionToken) return;

    try {
      setLoading(true);
      await performAction(action, sessionToken);
      // Refresh game state immediately after action
      await fetchGameState();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return {
    gameState,
    loading,
    error,
    refresh: fetchGameState,
    executeAction,
  };
};
