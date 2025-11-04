import React, { useState } from 'react';
import Button from '../UI/Button';
import { createGame } from '../../services/api';
import { useTelegram } from '../../hooks/useTelegram';

interface CreateGameProps {
  sessionToken: string;
  onCreated?: (gameId: string) => void;
}

const STAKE_OPTIONS = [
  { value: 'micro', label: 'Micro (5/10)' },
  { value: 'low', label: 'Low (10/20)' },
  { value: 'medium', label: 'Medium (25/50)' },
  { value: 'high', label: 'High (50/100)' },
  { value: 'premium', label: 'Premium (100/200)' },
];

const CreateGame: React.FC<CreateGameProps> = ({ sessionToken, onCreated }) => {
  const [stakeLevel, setStakeLevel] = useState(STAKE_OPTIONS[0].value);
  const [status, setStatus] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const { hapticFeedback } = useTelegram();

  const handleCreate = async () => {
    if (!sessionToken) return;

    setLoading(true);
    setStatus('Creating game...');

    try {
      const response = await createGame(stakeLevel, sessionToken);
      setStatus(`✅ Game created (${response.game_id.slice(0, 8)}…)`);
      hapticFeedback('medium');
      onCreated?.(response.game_id);
    } catch (error) {
      console.error('Failed to create game', error);
      setStatus('❌ Failed to create game. Please try again.');
      hapticFeedback('heavy');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="create-game">
      <h2>Create a Game</h2>
      <label htmlFor="stake-level">Stake Level</label>
      <select
        id="stake-level"
        value={stakeLevel}
        onChange={(event) => setStakeLevel(event.target.value)}
        disabled={loading}
      >
        {STAKE_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      <Button onClick={handleCreate} disabled={loading} fullWidth>
        {loading ? 'Creating…' : 'Create Game'}
      </Button>

      {status && <p className="create-game-status">{status}</p>}
    </div>
  );
};

export default CreateGame;
