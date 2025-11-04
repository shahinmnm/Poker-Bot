import React, { useState, useEffect, useCallback } from 'react'
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
  const [gameState, setGameState] = useState<GameState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [authenticated, setAuthenticated] = useState(false)
  const [currentUserId, setCurrentUserId] = useState<number | null>(null)
  const [raiseAmount, setRaiseAmount] = useState<number>(0)

  const loadGames = useCallback(async () => {
    try {
      const response = await fetch('/api/game/list', {
        credentials: 'include'
      })

      if (!response.ok) {
        throw new Error(`Failed to load games: ${response.status}`)
      }

      const data = await response.json()
      const gameList = (Array.isArray(data) ? data : data.games || []) as Game[]
      setGames(gameList)
      setError(null)
    } catch (err) {
      console.error('Failed to load games:', err)
      setError('❌ Failed to load games')
    }
  }, [])

  useEffect(() => {
    const initSession = async () => {
      try {
        const response = await fetch('/api/auth/login', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' }
        })

        if (!response.ok) {
          console.error('❌ Login failed:', response.status)
          setError('🔒 Authentication failed. Please refresh.')
          setAuthenticated(false)
          return
        }

        const data = await response.json()
        console.log('✅ Session created:', data)
        setCurrentUserId(data?.user_id ?? null)
        setAuthenticated(true)
        await loadGames()
      } catch (err) {
        console.error('❌ Session init error:', err)
        setError('🔒 Authentication failed. Please refresh.')
        setAuthenticated(false)
      } finally {
        setLoading(false)
      }
    }

    initSession()
  }, [loadGames])

  const handleCreateGame = async () => {
    try {
      const response = await fetch('/api/game/create', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: 'group',
          buy_in: 100
        })
      })

      if (!response.ok) throw new Error('Failed to create game')

      await loadGames()
    } catch (err) {
      console.error('Failed to create game:', err)
      setError('❌ Failed to create game')
    }
  }

  const handleJoinGame = async (gameId: string) => {
    try {
      const response = await fetch('/api/game/join', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ game_id: gameId })
      })

      if (!response.ok) throw new Error('Failed to join')

      const state = await response.json()
      setGameState(state)
      setView('game')
      setRaiseAmount(0)
      setError(null)
    } catch (err) {
      console.error('Failed to join game:', err)
      setError('❌ Failed to join game')
    }
  }

  const handleReady = async () => {
    if (!gameState) return

    try {
      const response = await fetch('/api/game/ready', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ game_id: gameState.game_id })
      })

      if (!response.ok) {
        throw new Error(`Ready failed: ${response.status}`)
      }
    } catch (err) {
      console.error('Ready failed:', err)
    }
  }

  const handleAction = async (action: string) => {
    if (!gameState) return

    if (action === 'raise' && raiseAmount < gameState.big_blind) {
      setError(`❌ Raise must be at least ${gameState.big_blind}`)
      return
    }

    setError(null)

    try {
      const response = await fetch('/api/game/action', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          game_id: gameState.game_id,
          action,
          amount: action === 'raise' ? raiseAmount : undefined
        })
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        const message =
          typeof errorData?.detail === 'string'
            ? errorData.detail
            : `Action failed: ${response.status}`
        throw new Error(message)
      }
    } catch (err) {
      console.error('Action failed:', err)
      const message = err instanceof Error ? err.message : 'Action failed'
      setError(`❌ ${message}`)
    }
  }

  const handleLeaveGame = async () => {
    if (!gameState) return

    const gameId = gameState.game_id

    try {
      await fetch(`/api/game/leave/${gameId}`, {
        method: 'POST',
        credentials: 'include'
      })
    } catch (err) {
      console.error('Failed to leave game:', err)
      setError('❌ Failed to leave game')
    } finally {
      setView('lobby')
      setGameState(null)
      setRaiseAmount(0)
      await loadGames()
    }
  }

  const currentGameId = gameState?.game_id

  useEffect(() => {
    if (!currentGameId || view !== 'game') return

    const poll = setInterval(async () => {
      try {
        const response = await fetch(`/api/game/state/${currentGameId}`, {
          credentials: 'include'
        })

        if (response.ok) {
          const state = await response.json()
          setGameState(state)
        }
      } catch (err) {
        console.error('Poll failed:', err)
      }
    }, 1500)

    return () => clearInterval(poll)
  }, [currentGameId, view])

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

  if (view === 'game' && gameState) {
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
                  onClick={() => handleAction('raise')}
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
        <button onClick={() => loadGames()} className="refresh-button">
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
