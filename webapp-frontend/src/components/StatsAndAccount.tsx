// webapp-frontend/src/components/StatsAndAccount.tsx
import React, { useEffect, useState } from "react";
import {
  BadgeCheckIcon,
  BellIcon,
  ChartIcon,
  CogIcon,
  FlameIcon,
  PlusIcon,
  ShieldIcon,
  WalletIcon,
  CoinsIcon,
  PercentIcon,
  UserIcon,
} from "./icons";
import { apiUserSettings, apiUserStats } from "../lib/api";

type Stats = {
  user_id: number;
  hands_played: number;
  biggest_win: number;
  biggest_loss: number;
  win_rate: number;
  last_played: string;
  streak_days: number;
  chip_balance: number;
  rank: string;
};

type Settings = {
  user_id: number;
  theme: "auto" | "dark" | "light";
  notifications: boolean;
  locale: string;
  currency: string;
  experimental: boolean;
};

export function StatsPanel() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const s = await apiUserStats();
        setStats(s as Stats);
        setErr(null);
      } catch (e: any) {
        setErr(e?.message || "Failed to load stats");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <div className="card">
      <div className="h">
        <ChartIcon width={18} height={18} />
        <span>Player Stats</span>
        {stats && <span className="sub">Rank: {stats.rank}</span>}
      </div>
      <div className="hr" />

      {loading && <div className="pill">Loading stats…</div>}
      {err && <div className="pill" style={{ color: "var(--error)" }}>{err}</div>}
      {stats && !err && (
        <div className="list">
          <div className="item">
            <div>
              <h4><FlameIcon width={16} height={16} style={{ marginRight: 6 }} /> Win rate</h4>
              <div className="meta">{Math.round(stats.win_rate * 100)}%</div>
            </div>
            <div className="pill"><PercentIcon width={14} height={14} /> consistency: {stats.streak_days}d</div>
          </div>
          <div className="item">
            <div>
              <h4><CoinsIcon width={16} height={16} style={{ marginRight: 6 }} /> Biggest win / loss</h4>
              <div className="meta">+{stats.biggest_win} · {stats.biggest_loss}</div>
            </div>
            <div className="pill">Hands: {stats.hands_played}</div>
          </div>
          <div className="item">
            <div>
              <h4><WalletIcon width={16} height={16} style={{ marginRight: 6 }} /> Chip balance</h4>
              <div className="meta">{stats.chip_balance}</div>
            </div>
            <div className="pill">{new Date(stats.last_played).toLocaleString()}</div>
          </div>
        </div>
      )}
    </div>
  );
}

export function AccountPanel() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const s = await apiUserSettings();
        setSettings(s as Settings);
        setErr(null);
      } catch (e: any) {
        setErr(e?.message || "Failed to load settings");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <div className="card">
      <div className="h">
        <CogIcon width={18} height={18} />
        <span>Account</span>
        {settings && <span className="sub">User #{settings.user_id}</span>}
      </div>
      <div className="hr" />

      {loading && <div className="pill">Loading account…</div>}
      {err && <div className="pill" style={{ color: "var(--error)" }}>{err}</div>}

      {settings && !err && (
        <div className="list">
          <div className="item">
            <div>
              <h4><BadgeCheckIcon width={16} height={16} style={{ marginRight: 6 }} /> Theme</h4>
              <div className="meta" style={{ textTransform: "capitalize" }}>{settings.theme}</div>
            </div>
            <div className="pill">{settings.experimental ? "Experimental On" : "Stable"}</div>
          </div>

          <div className="item">
            <div>
              <h4><BellIcon width={16} height={16} style={{ marginRight: 6 }} /> Notifications</h4>
              <div className="meta">{settings.notifications ? "Enabled" : "Disabled"}</div>
            </div>
            <div className="pill">{settings.locale}</div>
          </div>

          <div className="item">
            <div>
              <h4><UserIcon width={16} height={16} style={{ marginRight: 6 }} /> Currency</h4>
              <div className="meta">{settings.currency}</div>
            </div>
            <button className="btn" title="Add funds (UI only)">
              <PlusIcon width={16} height={16} />
              Add chips
            </button>
          </div>

          <div className="item">
            <div>
              <h4><ShieldIcon width={16} height={16} style={{ marginRight: 6 }} /> Privacy</h4>
              <div className="meta">Reasonable defaults</div>
            </div>
            <div className="pill">Manage</div>
          </div>
        </div>
      )}
    </div>
  );
}
