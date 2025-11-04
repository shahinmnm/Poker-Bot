import React from 'react';
import { GameListItem } from '../../types/game.types';
import { formatTimeAgo } from '../../utils/formatters';

interface GameCardProps {
  game: GameListItem;
  onSelect: () => void;
}

const GameCard: React.FC<GameCardProps> = ({ game, onSelect }) => {
  return (
    <div className="game-card" onClick={onSelect}>
      <div className="game-card-header">
        <span className="game-stake">{game.stake}</span>
        <span className="game-time">{formatTimeAgo(game.created_at)}</span>
      </div>

      <div className="game-card-body">
        <div className="game-host">
          <span className="host-icon">👑</span>
          <span className="host-name">{game.host}</span>
        </div>

        <div className="game-players">
          <span className="players-icon">👥</span>
          <span className="players-count">
            {game.player_count}/{game.max_players}
          </span>
        </div>
      </div>

      <div className="game-card-footer">
        <button className="join-button">Join Game</button>
      </div>
    </div>
  );
};

export default GameCard;
