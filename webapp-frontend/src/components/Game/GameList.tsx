import React, { useEffect, useState } from 'react';
import { GameListItem } from '../../types/game.types';
import { getGameList } from '../../services/api';
import { useTelegram } from '../../hooks/useTelegram';
import GameCard from './GameCard';
import Loading from '../UI/Loading';

interface GameListProps {
  sessionToken: string;
  onSelectGame: (gameId: string) => void;
  refreshToken?: number;
}

const GameList: React.FC<GameListProps> = ({ sessionToken, onSelectGame, refreshToken = 0 }) => {
  const [games, setGames] = useState<GameListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { hapticFeedback } = useTelegram();

  useEffect(() => {
    const fetchGames = async () => {
      try {
        setLoading(true);
        const response = await getGameList(sessionToken);
        setGames(response);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load games');
      } finally {
        setLoading(false);
      }
    };

    fetchGames();
    const interval = setInterval(fetchGames, 5_000);
    return () => clearInterval(interval);
  }, [sessionToken, refreshToken]);

  const handleSelectGame = (gameId: string) => {
    hapticFeedback('light');
    onSelectGame(gameId);
  };

  if (loading) return <Loading message="Loading games..." />;
  if (error) return <div className="error-message">{error}</div>;

  return (
    <div className="game-list">
      <div className="game-list-header">
        <h2>🎰 Available Games</h2>
        <span className="game-count">
          {games.length} game{games.length !== 1 ? 's' : ''}
        </span>
      </div>

      {games.length === 0 ? (
        <div className="empty-state">
          <p>🃏 No games available</p>
          <p className="empty-hint">Start a new game from Telegram or using the form below.</p>
        </div>
      ) : (
        <div className="games-grid">
          {games.map((game) => (
            <GameCard key={game.game_id} game={game} onSelect={() => handleSelectGame(game.game_id)} />
          ))}
        </div>
      )}
    </div>
  );
};

export default GameList;
