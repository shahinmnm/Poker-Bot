import React, { useEffect, useMemo, useState } from 'react';
import { applyUXSettings, setFourColorDeck, setHapticsEnabled, haptics } from '../utils/uiEnhancers';

/**
 * StatsAndAccount.tsx
 * Mini-app panels for:
 *  - Player Stats (hands played, win rate, profit, biggest pot, distribution, streak)
 *  - Account Settings (UX toggles important for gameplay + daily bonus + session info)
 *
 * Design goals
 *  - Minimal and easy to scan, with poker-friendly tone
 *  - Auto dark/light via Telegram theme vars and prefers-color-scheme
 *  - No size change; uses parent container’s width/height
 *  - Works even if backend endpoints aren’t ready (graceful fallback with hints)
 *
 * Integration plan:
 *  - Imported by App.tsx tabs (Stats & Account)
 *  - Expects sessionToken (string), userId (number | null), username (string | null)
 *
 * Server endpoints (optional; guarded if missing):
 *  - GET  /api/user/stats
 *  - GET  /api/user/settings
 *  - POST /api/user/settings
 *  - POST /api/user/bonus
 */

type Nullable<T> = T | null;

type StatsResponse = {
  hands_played: number;
  hands_won: number;
  total_profit: number;       // can be negative/positive
  biggest_pot_won: number;
  avg_stake: number;          // average table blind/BB unit
  current_streak: number;     // consecutive wins (optional)
  hand_distribution?: Record<string, number>; // e.g. { "Pair": 12, "Flush": 2, ... }
};

type Settings = {
  // gameplay / UX toggles (the ones players care about in a fast Telegram WebApp)
  fourColorDeck: boolean;
  showHandStrength: boolean;
  confirmAllIn: boolean;
  autoCheckFold: boolean;
  haptics: boolean;
  // future-proof room for account prefs
};

type SettingsResponse = Settings & {
  // also return balance/stack info if backend has it
  balance?: number;
};

type BonusResponse = {
  success: boolean;
  amount?: number;       // chips granted
  next_claim_at?: string;
  message?: string;
};

type Props = {
  sessionToken: Nullable<string>;
  userId: Nullable<number>;
  username: Nullable<string>;
};

/** Utilities */
const fmtNum = (n: number | undefined | null, digits = 0) =>
  typeof n === 'number' && !Number.isNaN(n) ? n.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—';

const pct = (num: number, den: number) => (den > 0 ? Math.round((num / den) * 100) : 0);

const defaultSettings: Settings = {
  fourColorDeck: true,
  showHandStrength: true,
  confirmAllIn: true,
  autoCheckFold: false,
  haptics: true,
};

const fallbackStats: StatsResponse = {
  hands_played: 0,
  hands_won: 0,
  total_profit: 0,
  biggest_pot_won: 0,
  avg_stake: 0,
  current_streak: 0,
  hand_distribution: {
    "High Card": 0,
    "Pair": 0,
    "Two Pair": 0,
    "Three of a Kind": 0,
    "Straight": 0,
    "Flush": 0,
    "Full House": 0,
    "Four of a Kind": 0,
    "Straight Flush": 0,
  },
};

/** Safe fetch with token + graceful errors */
async function safeFetch<T>(
  path: string,
  opts: RequestInit,
  token?: string | null,
): Promise<{ ok: boolean; data?: T; status: number }> {
  try {
    const res = await fetch(path, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        ...(opts.headers || {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      credentials: 'include',
    });
    if (!res.ok) return { ok: false, status: res.status };
    const data = (await res.json()) as T;
    return { ok: true, data, status: res.status };
  } catch {
    return { ok: false, status: 0 };
  }
}

/** Small UI primitives */
const Row: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div style={{
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '10px 12px',
    borderRadius: 10,
    background: 'var(--tg-theme-secondary-bg-color, rgba(255,255,255,0.04))',
    marginBottom: 8,
  }}>
    <span style={{ opacity: 0.85, fontSize: 14 }}>{label}</span>
    <span style={{ fontWeight: 600, fontSize: 14 }}>{value}</span>
  </div>
);

const SectionTitle: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ fontSize: 13, opacity: 0.7, margin: '18px 2px 8px' }}>{children}</div>
);

const Toggle: React.FC<{ checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }> = ({ checked, onChange, label, hint }) => (
  <label style={{
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '12px', borderRadius: 12,
    background: 'var(--tg-theme-secondary-bg-color, rgba(255,255,255,0.04))',
    marginBottom: 8, cursor: 'pointer'
  }}>
    <div>
      <div style={{ fontSize: 14, fontWeight: 600 }}>{label}</div>
      {hint && <div style={{ fontSize: 12, opacity: 0.7, marginTop: 2 }}>{hint}</div>}
    </div>
    <input
      type="checkbox"
      checked={checked}
      onChange={(e) => {
        onChange(e.target.checked);
        // UX cue
        haptics.selection();
      }}
      style={{ width: 20, height: 20 }}
      aria-label={label}
    />
  </label>
);

const Button: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement>> = ({ children, style, onClick, ...rest }) => (
  <button
    {...rest}
    onClick={(e) => {
      haptics.click();
      onClick?.(e);
    }}
    style={{
      width: '100%',
      padding: '12px 14px',
      borderRadius: 12,
      border: '1px solid rgba(255,255,255,0.12)',
      background: 'linear-gradient(180deg, rgba(40,40,40,0.8), rgba(20,20,20,0.8))',
      color: 'var(--tg-theme-text-color, #fff)',
      fontWeight: 700,
      fontSize: 14,
      boxShadow: '0 6px 18px rgba(0,0,0,0.25)',
      ...style,
    }}
  >
    {children}
  </button>
);

/** Progress bar for hand distribution */
const ProgressBar: React.FC<{ label: string; value: number; max: number }> = ({ label, value, max }) => {
  const p = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4, opacity: 0.8 }}>
        <span>{label}</span>
        <span>{value}</span>
      </div>
      <div style={{ height: 8, borderRadius: 6, background: 'rgba(255,255,255,0.1)' }}>
        <div style={{
          width: `${p}%`,
          height: '100%',
          borderRadius: 6,
          background: 'linear-gradient(90deg, #2ecc71, #27ae60)',
          transition: 'width .3s ease',
        }} />
      </div>
    </div>
  );
};

/** Stats Panel */
export const StatsPanel: React.FC<Props> = ({ sessionToken, userId, username }) => {
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<StatsResponse>(fallbackStats);
  const [serverAvailable, setServerAvailable] = useState(true);

  useEffect(() => {
    let mounted = true;
    (async () => {
      setLoading(true);
      const res = await safeFetch<StatsResponse>('/api/user/stats', { method: 'GET' }, sessionToken);
      if (!mounted) return;
      if (res.ok && res.data) {
        setStats({
          ...fallbackStats,
          ...res.data,
          hand_distribution: res.data.hand_distribution ?? fallbackStats.hand_distribution,
        });
        setServerAvailable(true);
      } else {
        setServerAvailable(false);
      }
      setLoading(false);
    })();
    return () => { mounted = false; };
  }, [sessionToken]);

  const winRate = useMemo(() => pct(stats.hands_won, stats.hands_played), [stats.hands_won, stats.hands_played]);

  const maxBucket = useMemo(() => {
    const dist = stats.hand_distribution || {};
    return Object.values(dist).reduce((m, v) => Math.max(m, v), 0);
  }, [stats.hand_distribution]);

  return (
    <div style={{ padding: 12 }}>
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 18, fontWeight: 800 }}>
          {username ? `@${username}` : 'Player'} {userId ? `#${userId}` : ''}
        </div>
        <div style={{ fontSize: 13, opacity: 0.7 }}>Career Stats</div>
      </div>

      {loading ? (
        <div style={{ padding: 24, textAlign: 'center', opacity: 0.7 }}>Loading stats…</div>
      ) : (
        <>
          {!serverAvailable && (
            <div style={{
              padding: 12, borderRadius: 12, marginBottom: 12,
              background: 'rgba(255, 199, 0, 0.12)', border: '1px solid rgba(255, 199, 0, 0.35)',
              color: 'var(--tg-theme-text-color, #fff)'
            }}>
              Server stats endpoint not found — showing local defaults. Once your backend
              exposes <code style={{ opacity: 0.8 }}>/api/user/stats</code>, this panel will auto-populate.
            </div>
          )}

          <Row label="Hands played" value={fmtNum(stats.hands_played)} />
          <Row label="Hands won" value={fmtNum(stats.hands_won)} />
          <Row label="Win rate" value={`${winRate}%`} />
          <Row label="Total profit" value={`${fmtNum(stats.total_profit)} 🪙`} />
          <Row label="Biggest pot won" value={`${fmtNum(stats.biggest_pot_won)} 🪙`} />
          <Row label="Avg stake" value={`${fmtNum(stats.avg_stake)} BB`} />
          <Row label="Current streak" value={`${fmtNum(stats.current_streak)} 🔥`} />

          <SectionTitle>Hand distribution</SectionTitle>
          <div style={{ padding: 12, borderRadius: 12, background: 'var(--tg-theme-secondary-bg-color, rgba(255,255,255,0.04))' }}>
            {Object.entries(stats.hand_distribution || {}).map(([k, v]) => (
              <ProgressBar key={k} label={k} value={v} max={maxBucket} />
            ))}
            {maxBucket === 0 && <div style={{ fontSize: 12, opacity: 0.7 }}>No hands recorded yet.</div>}
          </div>
        </>
      )}
    </div>
  );
};

/** Account Panel */
export const AccountPanel: React.FC<Props> = ({ sessionToken, userId, username }) => {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState<Settings>(defaultSettings);
  const [balance, setBalance] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [serverAvailable, setServerAvailable] = useState(true);

  // Load settings, then apply UX effects immediately
  useEffect(() => {
    let mounted = true;
    (async () => {
      setLoading(true);
      const res = await safeFetch<SettingsResponse>('/api/user/settings', { method: 'GET' }, sessionToken);
      if (!mounted) return;
      if (res.ok && res.data) {
        const { balance: b, ...rest } = res.data;
        const merged: Settings = { ...defaultSettings, ...rest };
        setSettings(merged);
        setBalance(typeof b === 'number' ? b : null);
        setServerAvailable(true);
        // Apply UX instantly
        applyUXSettings({ fourColorDeck: merged.fourColorDeck, haptics: merged.haptics });
      } else {
        setServerAvailable(false);
        // Also apply defaults so UX reflects toggles even without server
        applyUXSettings({ fourColorDeck: defaultSettings.fourColorDeck, haptics: defaultSettings.haptics });
      }
      setLoading(false);
    })();
    return () => { mounted = false; };
  }, [sessionToken]);

  const updateSetting = <K extends keyof Settings>(key: K, val: Settings[K]) => {
    setSettings((prev) => {
      const next = { ...prev, [key]: val };
      // Live-apply UX toggles
      if (key === 'fourColorDeck') setFourColorDeck(!!val);
      if (key === 'haptics') setHapticsEnabled(!!val);
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    setMessage(null);
    const res = await safeFetch<SettingsResponse>('/api/user/settings', {
      method: 'POST',
      body: JSON.stringify(settings),
    }, sessionToken);
    if (res.ok) {
      setMessage('Settings saved ✅');
      if (res.data?.balance != null) setBalance(res.data.balance);
      haptics.notification('success');
    } else {
      setMessage('Could not save settings. They will still apply locally.');
      haptics.notification('warning');
    }
    setSaving(false);
  };

  const claimBonus = async () => {
    setMessage(null);
    const res = await safeFetch<BonusResponse>('/api/user/bonus', { method: 'POST' }, sessionToken);
    if (res.ok && res.data?.success) {
      setMessage(`Bonus +${fmtNum(res.data.amount)} 🪙 claimed!`);
      if (typeof res.data.amount === 'number' && balance != null) {
        setBalance(balance + res.data.amount);
      }
      haptics.notification('success');
    } else {
      setMessage(res.data?.message || 'Bonus not available right now.');
      haptics.notification('warning');
    }
  };

  return (
    <div style={{ padding: 12 }}>
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 18, fontWeight: 800 }}>
          {username ? `@${username}` : 'Player'} {userId ? `#${userId}` : ''}
        </div>
        <div style={{ fontSize: 13, opacity: 0.7 }}>Account & Settings</div>
      </div>

      {loading ? (
        <div style={{ padding: 24, textAlign: 'center', opacity: 0.7 }}>Loading…</div>
      ) : (
        <>
          {!serverAvailable && (
            <div style={{
              padding: 12, borderRadius: 12, marginBottom: 12,
              background: 'rgba(255, 199, 0, 0.12)', border: '1px solid rgba(255, 199, 0, 0.35)',
              color: 'var(--tg-theme-text-color, #fff)'
            }}>
              Server settings endpoint not found — using local defaults. When you add
              <code style={{ opacity: 0.8 }}> /api/user/settings </code> this will sync automatically.
            </div>
          )}

          <SectionTitle>Profile</SectionTitle>
          <Row label="Username" value={username ? `@${username}` : '—'} />
          <Row label="User ID" value={userId ?? '—'} />
          <Row label="Balance" value={balance != null ? `${fmtNum(balance)} 🪙` : '—'} />

          <SectionTitle>Gameplay & UX</SectionTitle>
          <Toggle
            checked={settings.fourColorDeck}
            onChange={(v) => updateSetting('fourColorDeck', v)}
            label="Four-color deck"
            hint="Separates suits by color for instant readability"
          />
          <Toggle
            checked={settings.showHandStrength}
            onChange={(v) => updateSetting('showHandStrength', v)}
            label="Show hand strength"
            hint="Inline equity/strength hints on your hand"
          />
          <Toggle
            checked={settings.confirmAllIn}
            onChange={(v) => updateSetting('confirmAllIn', v)}
            label="Confirm all-in"
            hint="Shows a confirmation before committing your entire stack"
          />
          <Toggle
            checked={settings.autoCheckFold}
            onChange={(v) => updateSetting('autoCheckFold', v)}
            label="Auto check/fold"
            hint="Pre-selects check or fold when legal to speed up play"
          />
          <Toggle
            checked={settings.haptics}
            onChange={(v) => updateSetting('haptics', v)}
            label="Haptic feedback"
            hint="Subtle taps on key events (deal, action, win)"
          />

          {message && (
            <div style={{
              padding: 10, borderRadius: 10, margin: '8px 0 0',
              background: 'rgba(46, 204, 113, 0.12)',
              border: '1px solid rgba(46, 204, 113, 0.35)',
              fontSize: 13
            }}>
              {message}
            </div>
          )}

          <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
            <Button disabled={saving} onClick={save}>
              {saving ? 'Saving…' : 'Save settings'}
            </Button>
            <Button onClick={claimBonus} style={{
              background: 'linear-gradient(180deg, rgba(34,34,34,0.9), rgba(18,18,18,0.9))',
              border: '1px solid rgba(255,215,0,0.45)',
              boxShadow: '0 8px 20px rgba(255,215,0,0.12)',
            }}>
              🎁 Claim daily bonus
            </Button>
          </div>
        </>
      )}
    </div>
  );
};

/** Combined convenience component (optional) */
const StatsAndAccount: React.FC<Props> = (props) => {
  const [tab, setTab] = useState<'stats' | 'account'>('stats');

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Segmented header */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 6,
        padding: 10,
        position: 'sticky',
        top: 0,
        zIndex: 2,
        background: 'var(--tg-theme-bg-color, #0f0f0f)',
      }}>
        <button
          onClick={() => { setTab('stats'); haptics.selection(); }}
          aria-pressed={tab === 'stats'}
          style={{
            padding: '10px 12px',
            borderRadius: 12,
            border: '1px solid rgba(255,255,255,0.12)',
            background: tab === 'stats'
              ? 'linear-gradient(180deg, rgba(39,174,96,0.8), rgba(27,135,73,0.8))'
              : 'var(--tg-theme-secondary-bg-color, rgba(255,255,255,0.04))',
            color: 'var(--tg-theme-text-color, #fff)',
            fontWeight: 800,
          }}
        >📊 Stats</button>
        <button
          onClick={() => { setTab('account'); haptics.selection(); }}
          aria-pressed={tab === 'account'}
          style={{
            padding: '10px 12px',
            borderRadius: 12,
            border: '1px solid rgba(255,255,255,0.12)',
            background: tab === 'account'
              ? 'linear-gradient(180deg, rgba(40,40,40,0.9), rgba(20,20,20,0.9))'
              : 'var(--tg-theme-secondary-bg-color, rgba(255,255,255,0.04))',
            color: 'var(--tg-theme-text-color, #fff)',
            fontWeight: 800,
          }}
        >👤 Account</button>
      </div>

      {/* Panel */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {tab === 'stats' ? <StatsPanel {...props} /> : <AccountPanel {...props} />}
      </div>
    </div>
  );
};

export default StatsAndAccount;
