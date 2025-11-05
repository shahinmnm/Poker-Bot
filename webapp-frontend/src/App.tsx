import React, { useEffect, useMemo, useState } from 'react';
import { LobbyPanel, GamePanel } from './components/LobbyAndGame';
import { StatsPanel, AccountPanel } from './components/StatsAndAccount';

/**
 * App.tsx
 * Top-level container for the Telegram Poker mini-app.
 *
 * Tabs:
 *  - Lobby  : table list + create/join flow (real endpoints if present, graceful mock if not)
 *  - Game   : active table HUD (uses selected table from Lobby)
 *  - Stats  : player stats panel (calls /api/user/stats)
 *  - Account: settings + daily bonus (calls /api/user/settings, /api/user/bonus)
 *
 * Theme:
 *  - Auto-adapts to Telegram WebApp theme variables with device fallback
 * Size:
 *  - Fills parent; no viewport hacks or global size changes
 */

/** Minimal types for Telegram WebApp */
declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData?: string;
        initDataUnsafe?: {
          user?: {
            id?: number;
            username?: string;
            first_name?: string;
            last_name?: string;
            language_code?: string;
          };
        };
        colorScheme?: 'light' | 'dark';
        themeParams?: Record<string, string>;
        expand?: () => void;
        ready?: () => void;
        isExpanded?: boolean;
        setHeaderColor?: (colorKey: string) => void; // 'bg_color' etc.
        setBackgroundColor?: (color: string) => void;
        onEvent?: (event: string, handler: (...args: any[]) => void) => void;
        offEvent?: (event: string, handler: (...args: any[]) => void) => void;
      };
    };
  }
}

/** Helpers */
const useTelegramEnv = () => {
  const [tgReady, setTgReady] = useState(false);
  const webapp = typeof window !== 'undefined' ? window.Telegram?.WebApp : undefined;

  useEffect(() => {
    try {
      webapp?.ready?.();
      // Expand once so we get the intended height within Telegram
      if (!webapp?.isExpanded) webapp?.expand?.();
      setTgReady(true);
    } catch {
      setTgReady(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const user = webapp?.initDataUnsafe?.user;
  // NOTE: In production your backend should validate initData; here we pass along for API auth.
  const sessionToken = webapp?.initData || null;

  return {
    tgReady,
    webapp,
    userId: user?.id ?? null,
    username: user?.username ?? null,
    sessionToken,
  };
};

type TabKey = 'lobby' | 'game' | 'stats' | 'account';

const App: React.FC = () => {
  const { tgReady, webapp, userId, username, sessionToken } = useTelegramEnv();
  const [tab, setTab] = useState<TabKey>('lobby');

  // Theme awareness: Telegram colorScheme -> fallback to prefers-color-scheme
  const isDark = useMemo(() => {
    const scheme = webapp?.colorScheme;
    if (scheme) return scheme === 'dark';
    if (typeof window !== 'undefined' && window.matchMedia) {
      return window.matchMedia('(prefers-color-scheme: dark)').matches;
    }
    return true; // poker-friendly default
  }, [webapp?.colorScheme]);

  // Optional: nudge header/background to match theme params if available
  useEffect(() => {
    if (!webapp) return;
    try {
      // @ts-expect-error: Telegram may accept color keys here
      webapp.setHeaderColor?.('bg_color');
      // If you want a custom background color instead of TG theme:
      // webapp.setBackgroundColor?.(isDark ? '#0f0f0f' : '#f7f7f7');
    } catch {
      /* noop */
    }
  }, [webapp, isDark]);

  // Shared container styles — keep size within parent (no viewport forcing)
  const appStyles: React.CSSProperties = {
    height: '100%',
    width: '100%',
    display: 'flex',
    flexDirection: 'column',
    background: 'var(--tg-theme-bg-color, ' + (isDark ? '#0f0f0f' : '#f7f7f7') + ')',
    color: 'var(--tg-theme-text-color, ' + (isDark ? '#ffffff' : '#111111') + ')',
    fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial, sans-serif',
  };

  const tabBarStyles: React.CSSProperties = {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, 1fr)',
    gap: 6,
    padding: 10,
    position: 'sticky',
    top: 0,
    zIndex: 10,
    background: 'var(--tg-theme-bg-color, ' + (isDark ? '#0f0f0f' : '#f7f7f7') + ')',
    borderBottom: '1px solid rgba(255,255,255,0.08)',
  };

  const tabBtn = (key: TabKey, label: string, activeGradient?: string) => {
    const active = tab === key;
    return (
      <button
        key={key}
        onClick={() => setTab(key)}
        aria-pressed={active}
        style={{
          padding: '10px 12px',
          borderRadius: 12,
          border: '1px solid rgba(255,255,255,0.12)',
          background: active
            ? (activeGradient ||
               (key === 'stats'
                 ? 'linear-gradient(180deg, rgba(39,174,96,0.8), rgba(27,135,73,0.8))'
                 : 'linear-gradient(180deg, rgba(40,40,40,0.9), rgba(20,20,20,0.9))'))
            : 'var(--tg-theme-secondary-bg-color, rgba(0,0,0,0.06))',
          color: 'var(--tg-theme-text-color, ' + (isDark ? '#fff' : '#111') + ')',
          fontWeight: 800,
          fontSize: 14,
          boxShadow: active ? '0 6px 18px rgba(0,0,0,0.25)' : 'none'
        }}
      >
        {label}
      </button>
    );
  };

  return (
    <div style={appStyles}>
      {/* Top Tab Bar */}
      <div style={tabBarStyles}>
        {tabBtn('lobby', '🏠 Lobby')}
        {tabBtn('game', '🃏 Game')}
        {tabBtn('stats', '📊 Stats', 'linear-gradient(180deg, rgba(39,174,96,0.8), rgba(27,135,73,0.8))')}
        {tabBtn('account', '👤 Account')}
      </div>

      {/* Content area */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {tab === 'lobby' && (
          <LobbyPanel
            sessionToken={sessionToken}
            userId={userId}
            username={username}
          />
        )}

        {tab === 'game' && (
          <GamePanel
            sessionToken={sessionToken}
            userId={userId}
            username={username}
          />
        )}

        {tab === 'stats' && (
          <StatsPanel
            sessionToken={sessionToken}
            userId={userId}
            username={username}
          />
        )}

        {tab === 'account' && (
          <AccountPanel
            sessionToken={sessionToken}
            userId={userId}
            username={username}
          />
        )}
      </div>

      {/* Footer (optional): session info for debugging; safe to remove */}
      <div style={{
        padding: 8,
        fontSize: 11,
        opacity: 0.55,
        textAlign: 'center',
        borderTop: '1px solid rgba(255,255,255,0.08)'
      }}>
        {tgReady ? 'Telegram WebApp ready' : 'Running without Telegram context'}
        {username ? ` • @${username}` : ''}{userId ? ` • id:${userId}` : ''}
      </div>
    </div>
  );
};

export default App;
