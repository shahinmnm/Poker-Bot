import React from 'react';
import { GameState } from '../../types/game.types';
import PlayersList from './PlayersList';
import CommunityCards from './CommunityCards';
import PlayerHand from './PlayerHand';
import BettingControls from './BettingControls';
import { formatChips } from '../../utils/cardUtils';
import { getGameStateText } from '../../utils/formatters';

interface GameTableProps {
  gameState: GameState;
  onAction: (action: string, amount?: number) => void;
}

const GameTable: React.FC<GameTableProps> = ({ gameState, onAction }) => {
  return (
    <div className="game-table">
      {/* Game Info Header */}
      <div className="game-header">
        <div className="game-state-badge">{getGameStateText(gameState.state)}</div>
        <div className="pot-display">
          <span className="pot-label">Pot:</span>
          <span className="pot-amount">💰 {formatChips(gameState.pot)}</span>
        </div>
      </div>

      {/* Players Around Table */}
      <PlayersList players={gameState.players} />

      {/* Community Cards */}
      <div className="table-center">
        <CommunityCards cards={gameState.community_cards} />
        {gameState.current_bet > 0 && (
          <div className="current-bet">Current bet: {formatChips(gameState.current_bet)}</div>
        )}
      </div>

      {/* Your Hand */}
      <PlayerHand cards={gameState.your_cards} />

      {/* Betting Controls */}
      {gameState.your_turn && (
        <BettingControls
          availableActions={gameState.available_actions}
          currentBet={gameState.current_bet}
          playerStack={gameState.players.find((p) => p.cards.length > 0)?.stack || 0}
          onAction={onAction}
        />
      )}

      {/* Turn Indicator */}
      {!gameState.your_turn && (
        <div className="waiting-indicator">
          <div className="waiting-spinner"></div>
          <span>Waiting for other players...</span>
        </div>
      )}
    </div>
  );
};

export default GameTable;
