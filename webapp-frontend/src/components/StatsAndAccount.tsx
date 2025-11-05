// webapp-frontend/src/components/StatsAndAccount.tsx
import React, { useEffect, useState } from "react";
import { apiUserSettings, apiUserStats, UserSettings, UserStats } from "../lib/api";
import {
  BadgeCheckIcon,
  BellIcon,
  ChartIcon,
  CoinsIcon,
  CogIcon,
  FlameIcon,
  PercentIcon,
  ShieldIcon,
  TrophyIcon,
  UserIcon,
  WalletIcon,
} from "./icons";

type Load<T> =
  | { status: "idle" | "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; code?: number; message?: string };

export function StatsPanel() {
  const [state, setState] = useState<Load<UserStats>>({ status: "loading" });

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await apiUserStats();
        if (!alive) return;
        setState({ status: "ready", data });
      } catch (e: any) {
        if (!alive) return;
        setState({ status: "error", code: e?.code, message: e?.message || String(e) });
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (state.status === "loading") return <div className="card muted">Loading stats…</div>;

  if (state.status === "error") {
    return (
      <div className="panel">
        <div className="panel-header">
          <div className="title">
            <TrophyIcon className="icon" /> Player Stats
          </div>
        </div>
        <div className="alert warn">
          {state.code === 404
            ? "Server stats endpoint not found — expose /api/user/stats."
            : state.code === 401
            ? "Open inside Telegram to authenticate and see your real stats."
            : "Could not load stats. Check server logs."}
        </div>
      </div>
    );
  }

  const s = state.data;
  return (
    <div className="panel">
      <div className="panel-header">
        <div className="title">
          <TrophyIcon className="icon" /> {s.rank}
        </div>
      </div>

      <div className="grid two">
        <div className="stat">
          <div className="stat-label">
            <ChartIcon className="icon sm" /> Hands played
          </div>
          <div className="stat-value">{s.hands_played.toLocaleString()}</div>
        </div>
        <div className="stat">
          <div className="stat-label">
            <PercentIcon className="icon sm" /> Win rate
          </div>
          <div className="stat-value">{Math.round(s.win_rate * 100)}%</div>
        </div>
        <div className="stat">
          <div className="stat-label">
            <FlameIcon className="icon sm" /> Current streak
          </div>
          <div className="stat-value">{s.streak_days} days</div>
        </div>
        <div className="stat">
          <div className="stat-label">
            <CoinsIcon className="icon sm" /> Chip balance
          </div>
          <div className="stat-value">{s.chip_balance.toLocaleString()}</div>
        </div>
      </div>

      <div className="card">
        <div className="kv">
          <div className="kv-k">Best win</div>
          <div className="kv-v">{s.biggest_win.toLocaleString()}</div>
        </div>
        <div className="kv">
          <div className="kv-k">Worst loss</div>
          <div className="kv-v">{s.biggest_loss.toLocaleString()}</div>
        </div>
        <div className="kv">
          <div className="kv-k">Last played</div>
          <div className="kv-v">{new Date(s.last_played).toLocaleString()}</div>
        </div>
      </div>
    </div>
  );
}

export function AccountPanel() {
  const [state, setState] = useState<Load<UserSettings>>({ status: "loading" });

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await apiUserSettings();
        if (!alive) return;
        setState({ status: "ready", data });
      } catch (e: any) {
        if (!alive) return;
        setState({ status: "error", code: e?.code, message: e?.message || String(e) });
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (state.status === "loading") return <div className="card muted">Loading account…</div>;

  if (state.status === "error") {
    return (
      <div className="panel">
        <div className="panel-header">
          <div className="title">
            <UserIcon className="icon" /> Account & Settings
          </div>
        </div>
        <div className="alert warn">
          {state.code === 404
            ? "Server settings endpoint not found — implement /api/user/settings."
            : state.code === 401
            ? "Open inside Telegram to authenticate and load your profile."
            : "Could not load account. Check server logs."}
        </div>
      </div>
    );
  }

  const p = state.data;
  return (
    <div className="panel">
      <div className="panel-header">
        <div className="title">
          <UserIcon className="icon" /> @{p.user_id} • Account & Settings
        </div>
      </div>

      <div className="grid two">
        <div className="stat">
          <div className="stat-label">
            <BadgeCheckIcon className="icon sm" /> Locale
          </div>
          <div className="stat-value">{p.locale}</div>
        </div>
        <div className="stat">
          <div className="stat-label">
            <BellIcon className="icon sm" /> Notifications
          </div>
          <div className="stat-value">{p.notifications ? "On" : "Off"}</div>
        </div>
        <div className="stat">
          <div className="stat-label">
            <WalletIcon className="icon sm" /> Currency
          </div>
          <div className="stat-value">{p.currency}</div>
        </div>
        <div className="stat">
          <div className="stat-label">
            <ShieldIcon className="icon sm" /> Experimental
          </div>
          <div className="stat-value">{p.experimental ? "Enabled" : "Disabled"}</div>
        </div>
      </div>

      <div className="card">
        <div className="kv">
          <div className="kv-k">Theme</div>
          <div className="kv-v">{p.theme}</div>
        </div>
        <div className="kv">
          <div className="kv-k">User ID</div>
          <div className="kv-v">{p.user_id}</div>
        </div>
        <div className="kv">
          <div className="kv-k">Preferences</div>
          <div className="kv-v">
            <CogIcon className="icon sm" /> Manage from Telegram client settings
          </div>
        </div>
      </div>
    </div>
  );
}
