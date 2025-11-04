import React, { useState, useEffect, useCallback, useRef } from 'react'
import './App.css'

type View = 'lobby' | 'game'

interface Game {
  id: string
  stake: string
  player_count: number
  mode: string
  status: string
}

interface Player {
  id: number
  name: string
  chips: number
  cards: string[]
  current_bet: number
  folded: boolean
  status: string
}

interface GameState {
  game_id: string
  players: Player[]
  pot: number
  community_cards: string[]
  current_turn: number
  phase: string
  current_bet: number
  small_blind: number
  big_blind: number
  ready_players: number[]
  winner_id?: number
}

function App() {
  const [view, setView] = useState<View>('lobby')
  const [games, setGames] = useState<Game[]>([])
  const [selectedGame, setSelectedGame] = useState<string | null>(null)
  const [gameState, setGameState] = useState<GameState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [authenticated, setAuthenticated] = useState(false)
  const [currentUserId, setCurrentUserId] = useState<number | null>(null)
  const [raiseAmount, setRaiseAmount] = useState<number>(0)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchGames = useCallback(
    async (force = false) => {
      if (!authenticated && !force) return

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
    },
    [authenticated]
  )

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
        setView('lobby')
      }
    } catch (err) {
      console.error('Fetch game state error:', err)
    }
  }, [])

  const initSession = useCallback(async () => {
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include'
      })

      if (response.ok) {
        const data = await response.json()
        setAuthenticated(true)
        setCurrentUserId(data.user_id ?? 1)
        await fetchGames(true)
      } else {
        setError('Failed to initialize session')
      }
    } catch (err: any) {
      setError(`Session error: ${err.message}`)
    } finally {
      setLoading(false)
    }
  }, [fetchGames])

  useEffect(() => {
    initSession()

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }
  }, [initSession])

  useEffect(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }

    if (selectedGame && authenticated && view === 'game') {
      fetchGameState(selectedGame)

      pollingRef.current = setInterval(() => {
        fetchGameState(selectedGame)
      }, 1500)
    }

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }
  }, [selectedGame, authenticated, view, fetchGameState])

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
        setView('game')
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
        body: JSON.stringify({ stake: '10/20', mode: 'group' })
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

  const handleReady = async () => {
    if (!selectedGame) return

    try {
      const response = await fetch('/api/game/ready', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ game_id: selectedGame })
      })

      if (response.ok) {
        await fetchGameState(selectedGame)
      }
    } catch (err: any) {
      setError(err.message)
    }
  }

  const handleAction = async (action: string, amount?: number) => {
    if (!selectedGame) return

    try {
      const response = await fetch('/api/game/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          game_id: selectedGame,
          action,
          amount
        })
      })

      if (response.ok) {
        await fetchGameState(selectedGame)
      } else {
        const errorData = await response.json()
        setError(errorData.detail || 'Action failed')
      }
    } catch (err: any) {
      setError(err.message)
    }
  }

  const handleLeaveGame = async () => {
    if (!selectedGame) return

    try {
      await fetch(`/api/game/leave/${selectedGame}`, {
        method: 'POST',
        credentials: 'include'
      })
    } catch (err: any) {
      setError(err.message)
    } finally {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }

      setView('lobby')
      setSelectedGame(null)
      setGameState(null)
      await fetchGames()
    }
  }

  const isMyTurn = () => {
    if (!gameState || currentUserId == null) return false
    const { current_turn: currentTurn, players } = gameState

    if (currentTurn < 0 || currentTurn >= players.length) return false
    return players[currentTurn]?.id === currentUserId
  }

  const getCurrentPlayer = (): Player | null => {
    if (!gameState || currentUserId == null) return null
    return gameState.players.find(player => player.id === currentUserId) || null
  }

  const canCheck = () => {
    const player = getCurrentPlayer()
    if (!player || !gameState) return false
    return player.current_bet === (gameState.current_bet ?? 0)
  }

  const callAmount = () => {
    const player = getCurrentPlayer()
    if (!player || !gameState) return 0
    const amount = (gameState.current_bet ?? 0) - player.current_bet
    return Math.max(0, amount)
  }

  if (loading) {
    return (
      <div className="container">
        <div className="loading">⏳ Initializing...</div>
      </div>
    )
  }

  if (!authenticated) {
    return (
      <div className="container">
        <div className="error-banner">🔒 Authentication failed. Please refresh.</div>
      </div>
    )
  }

  if (view === 'game' && gameState && selectedGame) {
    const currentPlayer = getCurrentPlayer()
    const myTurn = isMyTurn()

    return (
      <div className="container">
        <div className="header">
          <button onClick={handleLeaveGame} className="back-button">
            ← Leave
          </button>
          <h2>🎮 {gameState.phase.toUpperCase()}</h2>
        </div>

        {error && <div className="error-banner">⚠️ {error}</div>}

        <div className="game-info">
          <div className="pot-display">
            <div className="pot-label">💰 POT</div>
            <div className="pot-amount">${gameState.pot}</div>
          </div>

          {gameState.phase === 'waiting' && (
            <button onClick={handleReady} className="ready-button">
              ✋ READY
            </button>
          )}
        </div>

        {gameState.community_cards.length > 0 && (
          <div className="community-section">
            <h3>🃏 Community Cards</h3>
            <div className="community-cards-display">
              {gameState.community_cards.map((card, idx) => (
                <div key={idx} className="card">
                  {card}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="players-section">
          <h3>👥 Players ({gameState.players.length}/9)</h3>
          <div className="players-grid">
            {gameState.players.map((player, idx) => (
              <div
                key={player.id}
                className={`player-card ${
                  gameState.current_turn === idx ? 'active-turn' : ''
                } ${player.folded ? 'folded' : ''} ${
                  player.id === currentUserId ? 'current-user' : ''
                }`}
              >
                <div className="player-name">
                  {player.name}
                  {player.id === currentUserId && ' (You)'}
                </div>

                <div className="player-chips">💎 ${player.chips}</div>

                {player.current_bet > 0 && (
                  <div className="player-bet">Bet: ${player.current_bet}</div>
                )}

                {player.cards && player.cards.length > 0 && (
                  <div className="player-cards">
                    {player.cards.map((card, cardIdx) => (
                      <span key={cardIdx} className="mini-card">
                        {card}
                      </span>
                    ))}
                  </div>
                )}

                {player.folded && <div className="player-status">❌ FOLDED</div>}
                {player.status === 'all_in' && (
                  <div className="player-status">🔥 ALL IN</div>
                )}
                {gameState.ready_players.includes(player.id) && (
                  <div className="player-status">✅ READY</div>
                )}
              </div>
            ))}
          </div>
        </div>

        {myTurn &&
          gameState.phase !== 'waiting' &&
          gameState.phase !== 'finished' && (
            <div className="action-panel">
              <h3>🎯 Your Turn</h3>

              <div className="action-buttons">
                <button onClick={() => handleAction('fold')} className="action-btn fold-btn">
                  ❌ Fold
                </button>

                {canCheck() ? (
                  <button onClick={() => handleAction('check')} className="action-btn check-btn">
                    ✅ Check
                  </button>
                ) : (
                  <button onClick={() => handleAction('call')} className="action-btn call-btn">
                    💵 Call ${callAmount()}
                  </button>
                )}

                <button onClick={() => handleAction('all_in')} className="action-btn allin-btn">
                  🔥 All In
                </button>
              </div>

              <div className="raise-controls">
                <input
                  type="number"
                  value={raiseAmount}
                  onChange={event => setRaiseAmount(Number(event.target.value))}
                  placeholder="Raise amount"
                  className="raise-input"
                  min={gameState.big_blind}
                  max={currentPlayer?.chips ?? 0}
                />
                <button
                  onClick={() => handleAction('raise', raiseAmount)}
                  className="action-btn raise-btn"
                  disabled={raiseAmount < gameState.big_blind}
                >
                  ⬆️ Raise
                </button>
              </div>
            </div>
          )}

        {gameState.phase === 'finished' && gameState.winner_id != null && (
          <div className="winner-banner">
            🏆 Winner:{' '}
            {gameState.players.find(player => player.id === gameState.winner_id)?.name || 'Unknown'}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="container">
      <h1>🃏 Poker Lobby</h1>

      {error && <div className="error-banner">⚠️ {error}</div>}

      <div className="button-group">
        <button onClick={() => fetchGames()} className="refresh-button">
          🔄 Refresh
        </button>
        <button onClick={handleCreateGame} className="create-button">
          ➕ Create Game
        </button>
      </div>

      <div className="games-list">
        {games.length === 0 ? (
          <div className="no-games">
            No active games.
            <br />
            Create one to get started!
          </div>
        ) : (
          games.map(game => (
            <div
              key={game.id}
              className="game-card"
              onClick={() => handleJoinGame(game.id)}
            >
              <div className="game-stake">💎 Stake: {game.stake}</div>
              <div className="game-players">👥 Players: {game.player_count}/9</div>
              <div className="game-mode">Mode: {game.mode}</div>
              <div className="game-status">Status: {game.status}</div>
              <div className="game-id">ID: {game.id.slice(0, 8)}...</div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default App
