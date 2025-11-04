import React, { useState } from 'react';
import { useTelegram } from '../../hooks/useTelegram';
import { formatChips } from '../../utils/cardUtils';
import Button from '../UI/Button';

interface BettingControlsProps {
  availableActions: string[];
  currentBet: number;
  playerStack: number;
  onAction: (action: string, amount?: number) => void;
}

const BettingControls: React.FC<BettingControlsProps> = ({
  availableActions,
  currentBet,
  playerStack,
  onAction,
}) => {
  const [betAmount, setBetAmount] = useState(currentBet * 2 || 100);
  const [showSlider, setShowSlider] = useState(false);
  const { hapticFeedback } = useTelegram();

  const handleAction = (action: string, amount?: number) => {
    hapticFeedback('medium');
    onAction(action, amount);
    setShowSlider(false);
  };

  const quickBetAmounts = [
    { label: '1/2 Pot', value: Math.floor(currentBet * 0.5) },
    { label: 'Pot', value: currentBet },
    { label: '2x Pot', value: currentBet * 2 },
  ];

  return (
    <div className="betting-controls">
      {showSlider ? (
        <div className="bet-slider-container">
          <div className="bet-amount-display">
            <span className="bet-label">Bet Amount:</span>
            <span className="bet-value">{formatChips(betAmount)}</span>
          </div>

          <input
            type="range"
            min={currentBet || 10}
            max={playerStack}
            value={betAmount}
            onChange={(e) => setBetAmount(Number(e.target.value))}
            className="bet-slider"
          />

          <div className="quick-bet-buttons">
            {quickBetAmounts.map((preset) => (
              <button
                key={preset.label}
                className="quick-bet-btn"
                onClick={() => setBetAmount(preset.value)}
              >
                {preset.label}
              </button>
            ))}
          </div>

          <div className="slider-actions">
            <Button variant="secondary" onClick={() => setShowSlider(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => handleAction('raise', betAmount)}>
              Confirm Bet
            </Button>
          </div>
        </div>
      ) : (
        <div className="action-buttons">
          {availableActions.includes('fold') && (
            <Button variant="danger" onClick={() => handleAction('fold')} icon="❌">
              Fold
            </Button>
          )}

          {availableActions.includes('check') && (
            <Button variant="secondary" onClick={() => handleAction('check')} icon="✓">
              Check
            </Button>
          )}

          {availableActions.includes('call') && (
            <Button variant="primary" onClick={() => handleAction('call')} icon="📞">
              Call {formatChips(currentBet)}
            </Button>
          )}

          {availableActions.includes('bet') && (
            <Button variant="success" onClick={() => setShowSlider(true)} icon="💰">
              Bet
            </Button>
          )}

          {availableActions.includes('raise') && (
            <Button variant="success" onClick={() => setShowSlider(true)} icon="📈">
              Raise
            </Button>
          )}
        </div>
      )}
    </div>
  );
};

export default BettingControls;
