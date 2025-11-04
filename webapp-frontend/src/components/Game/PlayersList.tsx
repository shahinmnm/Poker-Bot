import React from 'react';
import { Player } from '../../types/game.types';
import { formatChips } from '../../utils/cardUtils';

interface PlayersListProps {
  players: Player[];
}

const PlayersList: React.FC<PlayersListProps> = ({ players }) => {
  return (
    <div className="players-list">
      {players.map((player, index) => {
        const seatClasses = [
          'player-seat',
          `seat-${index}`,
          player.is_current ? 'current-turn' : '',
          !player.is_active ? 'folded' : '',
        ]
          .filter(Boolean)
          .join(' ');

        return (
          <div key={player.user_id} className={seatClasses}>
            <div className="player-avatar">
              {player.is_current && <div className="turn-indicator">⏰</div>}
              <span className="avatar-emoji">{player.is_active ? '🧑' : '💤'}</span>
            </div>

            <div className="player-info">
              <div className="player-name">{player.username}</div>
              <div className="player-stack">💵 {formatChips(player.stack)}</div>
            </div>

            {player.bet > 0 && <div className="player-bet">Bet: {formatChips(player.bet)}</div>}

            {player.cards.length > 0 && (
              <div className="player-cards">
                {player.cards.map((card, i) => (
                  <span key={i} className="mini-card">
                    🂠
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default PlayersList;
