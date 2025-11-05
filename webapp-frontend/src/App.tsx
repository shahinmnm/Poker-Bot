import React, { useEffect, useMemo, useState } from 'react';
import StatsAndAccount, { StatsPanel, AccountPanel } from './components/StatsAndAccount';

/**
 * App.tsx
 * Top-level container for the Telegram Poker mini-app.
 *
 * Features
 *  - Four tabs: Lobby | Game | Stats | Account
 *  - Uses Telegram WebApp context to read user/session
 *  - Dark/Light theme adapts via Telegram theme variables and prefers-color-scheme
 *  - Keeps mini-app size unchanged (fills parent; no global viewport hacks)
 *  - Zero external CSS dependency; inline styles respect WebApp theme
 *
 * How it works
 *  - We read window.Telegram.WebApp?.initDataUnsafe for user info
 *  - We treat initData string as a bearer `sessionToken` (customize if your backend differs)
 *  - The existing app content (if any) can live under Lobby/Game stubs for now
 *    (replace the placeholders with your real components at your pace)
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
  // NOTE: You may want to validate/parse initData server-side; here we pass as-is for demo.
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
    return true; // default to dark for poker vibe
  }, [webapp?.colorScheme]);

  // Optional: nudge header/background to match theme params if available
  useEffect(() => {
    if (!webapp) return;
    try {
      // @ts-expect-error: Telegram may accept color keys here
      webapp.setHeaderColor?.('bg_color');
      // If you want a custom background, uncomment below:
      // webapp.setBackgroundColor?.(isDark ? '#0f0f0f' : '#f7f7f7');
    } catch {
      /* noop */
    }
  }, [webapp, isDark]);

  // Basic placeholders for Lobby/Game to avoid compile errors.
  // Replace these with your real components/screens at any time.
  const LobbyView = (
    <div style={{ padding: 12 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 6 }}>Lobby</div>
      <div style={{ fontSize: 13, opacity: 0.75 }}>
        Create or join private games, invite friends, and browse active tables.
      </div>
      <div style={{
        marginTop: 12,
        padding: 12,
        borderRadius: 12,
        background: 'var(--tg-theme-secondary-bg-color, rgba(255,255,255,0.04))',
        border: '1px solid rgba(255,255,255,0.12)'
      }}>
        <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6 }}>
          <li>Quick Match (6-max, 1/2 BB)</li>
          <li>Friends Table (Invite-only)</li>
          <li>Deep Stack (2/5 BB)</li>
        </ul>
      </div>
    </div>
  );

  const GameView = (
    <div style={{ padding: 12 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 6 }}>Game</div>
      <div style={{ fontSize: 13, opacity: 0.75 }}>
        Your active table appears here. Replace this with your real table UI.
      </div>
      <div style={{
        marginTop: 12,
        padding: 12,
        borderRadius: 12,
        background: 'var(--tg-theme-secondary-bg-color, rgba(255,255,255,0.04))',
        border: '1px solid rgba(255,255,255,0.12)'
      }}>
        <div style={{ fontSize: 12, opacity: 0.8 }}>
          Tip: enable “Four-color deck” in Account → Settings for faster suit recognition.
        </div>
      </div>
    </div>
  );

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
        {tab === 'lobby' && LobbyView}
        {tab === 'game' && GameView}
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
