// webapp-frontend/src/App.tsx
import React, { useEffect, useMemo, useState } from 'react';
import { LobbyPanel, GamePanel } from './components/LobbyAndGame';
import { StatsPanel, AccountPanel } from './components/StatsAndAccount';
import './styles/theme.css';
import { CardsIcon, PlayIcon, ChartIcon, UserIcon } from './components/icons';

/**
 * App shell with top navigation (Lobby, Game, Stats, Account)
 * - Theme handling: system (prefers-color-scheme) + Telegram WebApp.colorScheme
 * - Keeps UI size minimal, but with a professional polish.
 */
type Tab = 'lobby' | 'game' | 'stats' | 'account';

export default function App() {
  const [tab, setTab] = useState<Tab>('lobby');

  // Apply theme class on document root
  useEffect(() => {
    const root = document.documentElement;

    function applyThemeFromSource(source: 'telegram' | 'system') {
      // @ts-ignore
      const tg = (window as any)?.Telegram?.WebApp;
      if (source === 'telegram' && tg?.colorScheme) {
        root.classList.remove('theme-light', 'theme-dark');
        root.classList.add(tg.colorScheme === 'dark' ? 'theme-dark' : 'theme-light');
        return;
      }
      // system fallback
      const prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
      root.classList.remove('theme-light', 'theme-dark');
      root.classList.add(prefersLight ? 'theme-light' : 'theme-dark');
    }

    // Initial
    applyThemeFromSource('telegram');

    // Listen for Telegram theme changes
    try {
      // @ts-ignore
      const tg = (window as any)?.Telegram?.WebApp;
      if (tg?.onEvent) {
        tg.onEvent('themeChanged', () => applyThemeFromSource('telegram'));
      }
    } catch {}

    // Listen for system theme changes as fallback
    const mq = window.matchMedia?.('(prefers-color-scheme: light)');
    const onChange = () => applyThemeFromSource('system');
    mq?.addEventListener?.('change', onChange);

    return () => {
      mq?.removeEventListener?.('change', onChange);
    };
  }, []);

  return (
    <div className="app">
      {/* Top nav */}
      <div className="nav">
        <button className={`tab ${tab === 'lobby' ? 'active' : ''}`} onClick={() => setTab('lobby')}>
          <CardsIcon /> Lobby
        </button>
        <button className={`tab ${tab === 'game' ? 'active' : ''}`} onClick={() => setTab('game')}>
          <PlayIcon /> Game
        </button>
        <button className={`tab ${tab === 'stats' ? 'active' : ''}`} onClick={() => setTab('stats')}>
          <ChartIcon /> Stats
        </button>
        <button className={`tab ${tab === 'account' ? 'active' : ''}`} onClick={() => setTab('account')}>
          <UserIcon /> Account
        </button>
      </div>

      {/* Content */}
      {tab === 'lobby' && <LobbyPanel />}
      {tab === 'game' && <GamePanel />}
      {tab === 'stats' && <StatsPanel />}
      {tab === 'account' && <AccountPanel />}

      {/* Footer */}
      <div className="footer">
        Telegram WebApp ready • @{(window as any)?.Telegram?.WebApp?.initDataUnsafe?.user?.username ?? 'guest'}
      </div>
    </div>
  );
}
