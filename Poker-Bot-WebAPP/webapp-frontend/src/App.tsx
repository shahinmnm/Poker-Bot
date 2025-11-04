import React, { useCallback, useEffect, useState } from 'react'
import './App.css'

interface GameSummary {
  game_id: string
  player_count: number
  max_players: number
  status: string
  stake_level?: string | null
  pot: number
}

interface PlayerInfo {
  user_id: number
  username: string
  chips: number
  is_active: boolean
}

interface GameState {
  game_id: string
  status: string
  players: PlayerInfo[]
  current_bet: number
  pot: number
  community_cards: string[]
  your_cards: string[]
  current_turn_user_id: number | null
}

function App() {
  const [games, setGames] = useState<GameSummary[]>([])
  const [selectedGameId, setSelectedGameId] = useState<string | null>(null)
  const [gameState, setGameState] = useState<GameState | null>(null)
  const [loadingLobby, setLoadingLobby] = useState(true)
  const [fetchingState, setFetchingState] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchGames = useCallback(async () => {
    try {
      setLoadingLobby(true)
      setError(null)
      const response = await fetch('/api/game/list')
      if (!response.ok) {
        throw new Error('Failed to load games')
      }
      const data = await response.json()
      const normalizedGames: GameSummary[] = Array.isArray(data)
        ? data
        : Array.isArray(data?.games)
        ? data.games
        : []
      setGames(normalizedGames)
    } catch (err) {
      console.error(err)
      setGames([])
      setError('Failed to load games')
    } finally {
      setLoadingLobby(false)
    }
  }, [])

  const fetchGameState = useCallback(
    async (gameId: string) => {
      try {
        setFetchingState(true)
        const response = await fetch(`/api/game/state/${gameId}`)
        if (response.status === 404) {
          setError('Game not found')
          setSelectedGameId(null)
          setGameState(null)
          return
        }
        if (!response.ok) {
          throw new Error('Failed to load game state')
        }
        const data: GameState = await response.json()
        setGameState(data)
        setError(null)
      } catch (err) {
        console.error(err)
        setError('Failed to load game state')
      } finally {
        setFetchingState(false)
      }
    },
    []
  )

  useEffect(() => {
    void fetchGames()
  }, [fetchGames])

  useEffect(() => {
    if (!selectedGameId) {
      return
    }

    void fetchGameState(selectedGameId)
    const interval = window.setInterval(() => {
      void fetchGameState(selectedGameId)
    }, 2000)

    return () => window.clearInterval(interval)
  }, [selectedGameId, fetchGameState])

  const handleJoinGame = async (gameId: string) => {
    try {
      setError(null)
      setGameState(null)
      const response = await fetch('/api/game/join', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ game_id: gameId }),
      })
      if (!response.ok) {
        throw new Error('Failed to join game')
      }
      setSelectedGameId(gameId)
      await fetchGameState(gameId)
    } catch (err) {
      console.error(err)
      setError('Failed to join game')
    }
  }

  const handleBackToLobby = () => {
    setSelectedGameId(null)
    setGameState(null)
    setError(null)
  }

  if (selectedGameId) {
    return (
      <div style={{ padding: '20px' }}>
        <button onClick={handleBackToLobby} style={{ marginBottom: '10px' }}>
          ← Back to Lobby
        </button>
        <h2>Game: {selectedGameId}</h2>
        {error && (
          <div style={{ color: 'red', marginBottom: '10px' }}>
            {error}
          </div>
        )}
        {fetchingState && !gameState ? (
          <div>Loading game state...</div>
        ) : gameState ? (
          <div>
            <div style={{ marginBottom: '15px' }}>
              <strong>Status:</strong> {gameState.status}
            </div>
            <h3>Players</h3>
            {gameState.players.length === 0 ? (
              <div>No players yet.</div>
            ) : (
              gameState.players.map((player) => (
                <div key={player.user_id} style={{ marginBottom: '6px' }}>
                  {player.username || `Player ${player.user_id}`}: ${player.chips}
                </div>
              ))
            )}
            <div style={{ marginTop: '15px' }}>
              <div>
                <strong>Pot:</strong> ${gameState.pot}
              </div>
              <div>
                <strong>Current Bet:</strong> ${gameState.current_bet}
              </div>
            </div>
            {fetchingState && (
              <div style={{ marginTop: '10px', fontSize: '12px', color: '#666' }}>
                Updating game state...
              </div>
            )}
          </div>
        ) : (
          <div>Game state unavailable.</div>
        )}
      </div>
    )
  }

  return (
    <div style={{ padding: '20px' }}>
      <h1>🃏 Poker Lobby</h1>
      {error && (
        <div style={{ color: 'red', marginBottom: '10px' }}>
          {error}
        </div>
      )}
      <div style={{ marginBottom: '10px' }}>
        <button onClick={() => void fetchGames()} disabled={loadingLobby}>
          🔄 Refresh
        </button>
      </div>
      {loadingLobby ? (
        <div>Loading games...</div>
      ) : games.length === 0 ? (
        <div>No games available.</div>
      ) : (
        <div>
          {games.map((game) => (
            <div
              key={game.game_id}
              style={{
                border: '1px solid #333',
                padding: '12px',
                marginBottom: '10px',
                borderRadius: '6px',
                cursor: 'pointer',
              }}
              onClick={() => void handleJoinGame(game.game_id)}
            >
              <div style={{ fontWeight: 600 }}>{game.stake_level || 'Unknown Stake'}</div>
              <div>Status: {game.status}</div>
              <div>
                Players: {game.player_count} / {game.max_players}
              </div>
              <div>Pot: ${game.pot}</div>
              <div style={{ fontSize: '12px', color: '#666' }}>ID: {game.game_id}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default App
