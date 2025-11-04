import React, { useState } from 'react';
import { useAuth } from './hooks/useAuth';
import { useGame } from './hooks/useGame';
import { useTelegram } from './hooks/useTelegram';
import GameList from './components/Game/GameList';
import GameTable from './components/Game/GameTable';
import CreateGame from './components/Game/CreateGame';
import Loading from './components/UI/Loading';
import { joinGame } from './services/api';
import './App.css';

function App() {
  const { isAuthenticated, sessionToken, loading: authLoading } = useAuth();
  const { user, hapticFeedback, showBackButton, hideBackButton } = useTelegram();
  const [currentGameId, setCurrentGameId] = useState<string | null>(null);
  const [listRefreshToken, setListRefreshToken] = useState(0);
  const { gameState, loading: gameLoading, executeAction } = useGame(currentGameId, sessionToken);

  const handleSelectGame = async (gameId: string) => {
    try {
      if (!sessionToken) return;

      hapticFeedback('medium');
      await joinGame(gameId, sessionToken);
      setCurrentGameId(gameId);
      showBackButton(() => {
        setCurrentGameId(null);
        hideBackButton();
      });
    } catch (error) {
      console.error('Failed to join game:', error);
      alert('Failed to join game. Please try again.');
    }
  };

  const handleGameAction = async (action: string, amount?: number) => {
    if (!currentGameId) return;

    try {
      await executeAction({
        game_id: currentGameId,
        action: action as any,
        amount,
      });
      hapticFeedback('light');
    } catch (error) {
      console.error('Action failed:', error);
      hapticFeedback('heavy');
      alert('Action failed. Please try again.');
    }
  };

  const handleGameCreated = () => {
    setListRefreshToken((value) => value + 1);
  };

  if (authLoading) {
    return <Loading message="Authenticating..." />;
  }

  if (!isAuthenticated) {
    return (
      <div className="auth-error">
        <h2>⚠️ Authentication Required</h2>
        <p>Please open this app from the Telegram bot.</p>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>🃏 Poker Game</h1>
        {user && <div className="user-info">Hello, {user.first_name}!</div>}
      </header>

      <main className="app-main">
        {!currentGameId ? (
          <>
            <CreateGame sessionToken={sessionToken!} onCreated={handleGameCreated} />
            <GameList
              sessionToken={sessionToken!}
              onSelectGame={handleSelectGame}
              refreshToken={listRefreshToken}
            />
          </>
        ) : gameState ? (
          <GameTable gameState={gameState} onAction={handleGameAction} />
        ) : (
          <Loading message="Loading game..." />
        )}

        {gameLoading && currentGameId && !gameState && <Loading message="Updating game..." />}
      </main>
    </div>
  );
}

export default App;
