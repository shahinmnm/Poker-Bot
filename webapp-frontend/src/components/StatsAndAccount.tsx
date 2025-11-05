// webapp-frontend/src/components/StatsAndAccount.tsx
import React, { useEffect, useState } from "react";
import {
  apiUserSettings,
  apiUserStats,
  SettingsDto,
  StatsDto,
} from "../lib/api";
import {
  BadgeCheckIcon,
  BellIcon,
  ChartIcon,
  CogIcon,
  FlameIcon,
  PlusIcon,
  ShieldIcon,
  TrophyIcon,
  WalletIcon,
} from "./icons";

export const StatsPanel: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<StatsDto | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const s = await apiUserStats();
      setStats(s);
    } catch (e: any) {
      if (e?.code === 401) {
        setError(
          "Open inside Telegram for your personal stats (authentication required)."
        );
      } else {
        setError("Failed to load stats.");
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <section className="panel">
      <header className="panel-header">
        <div className="title">
          <TrophyIcon className="icon" /> Player Stats
        </div>
        <button className="btn ghost" onClick={refresh} disabled={loading}>
          ↻ Refresh
        </button>
      </header>

      {error && <div className="alert warn">{error}</div>}

      {loading ? (
        <div className="muted">Loading…</div>
      ) : stats ? (
        <div className="grid two">
          <div className="stat">
            <div className="stat-label">
              <BadgeCheckIcon className="icon sm" /> Rank
            </div>
            <div className="stat-value">{stats.rank}</div>
          </div>
          <div className="stat">
            <div className="stat-label">
              <FlameIcon className="icon sm" /> Streak
            </div>
            <div className="stat-value">{stats.streak_days} days</div>
          </div>

          <div className="stat">
            <div className="stat-label">
              <ChartIcon className="icon sm" /> Win rate
            </div>
            <div className="stat-value">{Math.round(stats.win_rate * 100)}%</div>
          </div>
          <div className="stat">
            <div className="stat-label">
              <WalletIcon className="icon sm" /> Chip balance
            </div>
            <div className="stat-value">{stats.chip_balance.toLocaleString()}</div>
          </div>

          <div className="stat">
            <div className="stat-label">Hands played</div>
            <div className="stat-value">{stats.hands_played}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Best / Worst</div>
            <div className="stat-value">
              {stats.biggest_win.toLocaleString()} • {stats.biggest_loss.toLocaleString()}
            </div>
          </div>
        </div>
      ) : (
        <div className="muted">No stats available.</div>
      )}
    </section>
  );
};

export const AccountPanel: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [settings, setSettings] = useState<SettingsDto | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const s = await apiUserSettings();
      setSettings(s);
    } catch (e: any) {
      if (e?.code === 401) {
        setError(
          "Open inside Telegram for full features and authentication (ACCOUNT)."
        );
      } else {
        setError("Failed to load account settings.");
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <section className="panel">
      <header className="panel-header">
        <div className="title">
          <CogIcon className="icon" /> Account & Settings
        </div>
        <button className="btn ghost" onClick={refresh} disabled={loading}>
          ↻ Refresh
        </button>
      </header>

      {error && <div className="alert warn">{error}</div>}

      {loading ? (
        <div className="muted">Loading…</div>
      ) : settings ? (
        <div className="card stack">
          <div className="kv">
            <div className="kv-k">User ID</div>
            <div className="kv-v">{settings.user_id}</div>
          </div>
          <div className="kv">
            <div className="kv-k">Locale</div>
            <div className="kv-v">{settings.locale}</div>
          </div>
          <div className="kv">
            <div className="kv-k">Theme</div>
            <div className="kv-v">{settings.theme}</div>
          </div>
          <div className="kv">
            <div className="kv-k">Notifications</div>
            <div className="kv-v">
              <BellIcon className="icon sm" /> {settings.notifications ? "On" : "Off"}
            </div>
          </div>
          <div className="kv">
            <div className="kv-k">Currency</div>
            <div className="kv-v">{settings.currency}</div>
          </div>
          <div className="kv">
            <div className="kv-k">Experimental</div>
            <div className="kv-v">{settings.experimental ? "Enabled" : "Disabled"}</div>
          </div>

          <div className="muted small">
            <ShieldIcon className="icon sm" /> Tip: Change your device theme; when your
            theme is <b>auto</b> the app follows Light/Dark automatically.
          </div>
          <button className="btn">
            <PlusIcon className="icon" /> Manage preferences
          </button>
        </div>
      ) : (
        <div className="muted">No account data.</div>
      )}
    </section>
  );
};
