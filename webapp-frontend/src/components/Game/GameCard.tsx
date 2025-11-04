import React from 'react';
import { GameListItem } from '../../types/game.types';
import { formatTimeAgo, getGameStateText } from '../../utils/formatters';

const STAKE_LABELS: Record<string, string> = {
  micro: 'Micro (5/10)',
  low: 'Low (10/20)',
  medium: 'Medium (25/50)',
  high: 'High (50/100)',
  premium: 'Premium (100/200)',
};

interface GameCardProps {
  game: GameListItem;
  onSelect: () => void;
}

const GameCard: React.FC<GameCardProps> = ({ game, onSelect }) => {
  const stakeLabel = (() => {
    if (!game.stake_level) {
      return `${game.small_blind}/${game.big_blind}`;
    }

    const normalized = game.stake_level.toLowerCase();
    return STAKE_LABELS[normalized] ?? game.stake_level;
  })();
  const createdLabel = game.created_at ? formatTimeAgo(game.created_at) : 'just now';
  const modeLabel = game.mode ? game.mode.charAt(0).toUpperCase() + game.mode.slice(1) : 'Unknown';
  const statusText = getGameStateText(game.status);
  const hostLabel = game.host ? `Host #${game.host}` : 'Host unknown';
  const potLabel = `Pot $${game.pot.toLocaleString()}`;

  return (
    <div className="game-card" onClick={onSelect}>
      <div className="game-card-header">
        <span className="game-stake">{stakeLabel}</span>
        <span className="game-time">{createdLabel}</span>
      </div>

      <div className="game-card-body">
        <div className="game-host">
          <span className="host-icon">👑</span>
          <span className="host-name">{hostLabel}</span>
        </div>

        <div className="game-details">
          <span className="detail-item">👥 {game.player_count}/{game.max_players}</span>
          <span className="detail-item">🎯 {modeLabel}</span>
          <span className="detail-item">💰 {potLabel}</span>
        </div>
      </div>

      <div className="game-card-footer">
        <span className="game-status">{statusText}</span>
        <button className="join-button">Join Game</button>
      </div>
    </div>
  );
};

export default GameCard;
