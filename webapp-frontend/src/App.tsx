import React, { useState, useEffect, useCallback } from 'react'
import './App.css'

interface Game {
  id: string
  stake: string
  player_count: number
}

interface GameState {
  game_id: string
  players: { id: number; name: string; chips: number }[]
  pot: number
  community_cards: string[]
  current_turn: number
  phase: string
}

function App() {
  const [games, setGames] = useState<Game[]>([])
  const [selectedGame, setSelectedGame] = useState<string | null>(null)
  const [gameState, setGameState] = useState<GameState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [authenticated, setAuthenticated] = useState(false)
  const [pollingInterval, setPollingInterval] = useState<NodeJS.Timeout | null>(null)

  // Initialize session on mount
  useEffect(() => {
    initSession()
    return () => {
      if (pollingInterval) clearInterval(pollingInterval)
    }
  }, [])

  // Setup polling when viewing a game
  useEffect(() => {
    if (pollingInterval) {
      clearInterval(pollingInterval)
      setPollingInterval(null)
    }

    if (selectedGame && authenticated) {
      fetchGameState(selectedGame)

      const interval = setInterval(() => {
        fetchGameState(selectedGame)
      }, 2000)

      setPollingInterval(interval)
    }

    return () => {
      if (pollingInterval) clearInterval(pollingInterval)
    }
  }, [selectedGame, authenticated])

  const initSession = async () => {
    try {
      // Try to create/get session
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include'
      })

      if (response.ok) {
        setAuthenticated(true)
        await fetchGames()
      } else {
        setError('Failed to initialize session')
      }
    } catch (err: any) {
      setError(`Session error: ${err.message}`)
    } finally {
      setLoading(false)
    }
  }

  const fetchGames = useCallback(async () => {
    if (!authenticated) return

    try {
      const response = await fetch('/api/game/list', {
        credentials: 'include'
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      const data = await response.json()
      setGames(data.games || [])
      setError(null)
    } catch (err: any) {
      setError(`Failed to load games: ${err.message}`)
      console.error('Fetch games error:', err)
    }
  }, [authenticated])

  const fetchGameState = useCallback(async (gameId: string) => {
    try {
      const response = await fetch(`/api/game/state/${gameId}`, {
        credentials: 'include'
      })

      if (response.ok) {
        const data = await response.json()
        setGameState(data)
        setError(null)
      } else if (response.status === 404) {
        setError('Game not found')
        setSelectedGame(null)
      }
    } catch (err: any) {
      console.error('Fetch game state error:', err)
    }
  }, [])

  const handleJoinGame = async (gameId: string) => {
    try {
      const response = await fetch('/api/game/join', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ game_id: gameId })
      })

      if (response.ok) {
        setSelectedGame(gameId)
      } else {
        throw new Error(`Failed to join: HTTP ${response.status}`)
      }
    } catch (err: any) {
      setError(err.message)
    }
  }

  const handleCreateGame = async () => {
    try {
      const response = await fetch('/api/game/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ stake: '1/2' })
      })

      if (response.ok) {
        await fetchGames()
      } else {
        throw new Error(`Failed to create: HTTP ${response.status}`)
      }
    } catch (err: any) {
      setError(err.message)
    }
  }

  const handleBackToLobby = () => {
    if (pollingInterval) {
      clearInterval(pollingInterval)
      setPollingInterval(null)
    }
    setSelectedGame(null)
    setGameState(null)
    fetchGames()
  }

  // Loading state
  if (loading) {
    return (
      <div className="container">
        <div className="loading">Initializing...</div>
      </div>
    )
  }

  if (!authenticated) {
    return (
      <div className="container">
        <div className="error">Authentication failed. Please refresh.</div>
      </div>
    )
  }

  // Game view
  if (selectedGame && gameState) {
    return (
      <div className="container">
        <div className="header">
          <button onClick={handleBackToLobby} className="back-button">
            ← Back to Lobby
          </button>
          <h2>Game: {selectedGame.slice(0, 8)}...</h2>
        </div>

        {error && <div className="error-banner">{error}</div>}

        <div className="game-view">
          <div className="pot">Pot: ${gameState.pot}</div>
          <div className="phase">Phase: {gameState.phase}</div>

          <div className="players">
            <h3>Players</h3>
            {gameState.players.map((p) => (
              <div key={p.id} className="player">
                {p.name}: ${p.chips}
              </div>
            ))}
          </div>

          {gameState.community_cards.length > 0 && (
            <div className="community-cards">
              <h3>Community Cards</h3>
              {gameState.community_cards.join(' ')}
            </div>
          )}
        </div>
      </div>
    )
  }

  // Lobby view
  return (
    <div className="container">
      <h1>🃏 Poker Lobby</h1>

      {error && <div className="error-banner">{error}</div>}

      <div className="button-group">
        <button onClick={fetchGames} className="refresh-button">
          🔄 Refresh
        </button>
        <button onClick={handleCreateGame} className="create-button">
          ➕ Create Game
        </button>
      </div>

      <div className="games-list">
        {games.length === 0 ? (
          <div className="no-games">No games available. Create one!</div>
        ) : (
          games.map((game) => (
            <div
              key={game.id}
              className="game-card"
              onClick={() => handleJoinGame(game.id)}
            >
              <div className="game-stake">Stake: {game.stake}</div>
              <div className="game-players">Players: {game.player_count}/9</div>
              <div className="game-id">ID: {game.id.slice(0, 8)}...</div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default App
