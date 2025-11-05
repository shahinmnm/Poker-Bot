// webapp-frontend/src/components/StatsAndAccount.tsx
//
// Named exports: StatsPanel, AccountPanel
// - Reads stats via apiUserStats()
// - Reads settings via apiUserSettings()
// - Friendly handling for 401 (AUTH_REQUIRED) and other HTTP errors
// - Imports ONLY icons that exist in ./icons.tsx

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiUserSettings, apiUserStats } from "../lib/api";
import {
  BadgeCheckIcon,
  BellIcon,
  ChartIcon,
  CoinsIcon,
  PercentIcon,
  SettingsIcon,
  ShieldIcon,
  TrophyIcon,
  UserIcon,
  WalletIcon,
  TrendingUpIcon,
  TrendingDownIcon,
} from "./icons";

/* ------------ Types (match backend responses you showed) ------------ */

type StatsDto = {
  user_id: number;
  hands_played: number;
  biggest_win: number;
  biggest_loss: number;
  win_rate: number; // 0..1
  last_played: string; // ISO string
  streak_days: number;
  chip_balance: number;
  rank: string;
};

type SettingsDto = {
  user_id: number;
  theme: "auto" | "light" | "dark";
  notifications: boolean;
  locale: string;
  currency: string; // "chips"
  experimental: boolean;
};

/* ------------------------- Small UI primitives ------------------------- */

function PanelShell(props: { title: React.ReactNode; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="w-full max-w-3xl mx-auto rounded-2xl border border-black/10 dark:border-white/10 bg-white/60 dark:bg-black/40 backdrop-blur p-4 md:p-6 shadow-sm">
      <header className="flex items-center justify-between gap-3 mb-4">
        <h2 className="text-base md:text-lg font-semibold flex items-center gap-2">{props.title}</h2>
        {props.right}
      </header>
      <div>{props.children}</div>
    </section>
  );
}

function StatCard(props: { label: string; value: React.ReactNode; icon?: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-black/10 dark:border-white/10 p-4 bg-black/[0.02] dark:bg-white/[0.04]">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs opacity-70">{props.label}</div>
        {props.icon}
      </div>
      <div className="text-lg font-semibold">{props.value}</div>
      {props.hint ? <div className="text-xs opacity-70 mt-1">{props.hint}</div> : null}
    </div>
  );
}

function Button(
  props: React.ButtonHTMLAttributes<HTMLButtonElement> & { left?: React.ReactNode; variant?: "soft" | "plain" }
) {
  const { className = "", left, variant = "soft", children, ...rest } = props;
  const base =
    "inline-flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium border transition active:translate-y-[0.5px]";
  const soft =
    "border-black/10 dark:border-white/15 bg-black/[0.04] dark:bg-white/[0.06] hover:bg-black/[0.06] dark:hover:bg-white/[0.10]";
  const plain = "border-transparent hover:bg-black/[0.05] dark:hover:bg-white/[0.06]";
  return (
    <button {...rest} className={`${base} ${variant === "plain" ? plain : soft} ${className}`}>
      {left}
      {children}
    </button>
  );
}

/* ------------------------------ Helpers ------------------------------ */

function pct(n: number) {
  if (!isFinite(n)) return "—";
  return `${Math.round(n * 100)}%`;
}
function fmtNumber(n: number) {
  try {
    return new Intl.NumberFormat().format(n);
  } catch {
    return String(n);
  }
}
function fmtDate(s: string) {
  try {
    const d = new Date(s);
    return d.toLocaleString();
  } catch {
    return s;
  }
}

/* ------------------------------- StatsPanel ------------------------------- */

type StatsState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "loaded"; data: StatsDto }
  | { kind: "error"; code?: number; message: string };

export function StatsPanel(props: any) {
  const [state, setState] = useState<StatsState>({ kind: "idle" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const data = (await apiUserStats()) as StatsDto;
      setState({ kind: "loaded", data });
    } catch (err: any) {
      if (err?.code === 401) {
        setState({
          kind: "error",
          code: 401,
          message: "Authentication required. Open inside Telegram (or use dev fallback user_id=1).",
        });
      } else if (typeof err?.code === "number") {
        setState({ kind: "error", code: err.code, message: `Server error (HTTP ${err.code}).` });
      } else {
        setState({ kind: "error", message: "Network error while loading stats." });
      }
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const body = useMemo(() => {
    if (state.kind === "idle" || state.kind === "loading") {
      return <div className="flex items-center justify-center h-32 text-sm opacity-70">Loading stats…</div>;
    }
    if (state.kind === "error") {
      return (
        <div className="space-y-3">
          <div className="rounded-xl border border-red-300/40 bg-red-50/60 dark:bg-red-900/20 p-3 text-sm">
            <div className="font-medium">Couldn’t load stats</div>
            <div className="opacity-80">
              {state.message}
              {state.code ? ` (code ${state.code})` : null}
            </div>
          </div>
          <Button onClick={load}>Try again</Button>
        </div>
      );
    }

    const s = state.data;
    const winrateUp = s.win_rate >= 0.5;

    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <StatCard
          label="Rank"
          value={
            <span className="inline-flex items-center gap-2">
              <BadgeCheckIcon className="w-5 h-5 opacity-80" />
              {s.rank}
            </span>
          }
          hint={`Streak: ${s.streak_days} day${s.streak_days === 1 ? "" : "s"}`}
        />
        <StatCard
          label="Hands Played"
          value={fmtNumber(s.hands_played)}
          icon={<ChartIcon className="w-5 h-5 opacity-60" />}
          hint={`Last played: ${fmtDate(s.last_played)}`}
        />
        <StatCard
          label="Win Rate"
          value={
            <span className="inline-flex items-center gap-1">
              {winrateUp ? (
                <TrendingUpIcon className="w-5 h-5 opacity-70" />
              ) : (
                <TrendingDownIcon className="w-5 h-5 opacity-70" />
              )}
              {pct(s.win_rate)}
            </span>
          }
          icon={<PercentIcon className="w-5 h-5 opacity-60" />}
          hint={winrateUp ? "On a heater" : "Variance happens"}
        />
        <StatCard
          label="Chip Balance"
          value={
            <span className="inline-flex items-center gap-2">
              <CoinsIcon className="w-5 h-5 opacity-80" />
              {fmtNumber(s.chip_balance)} {/** currency is "chips" */}
            </span>
          }
          hint={`Best win: ${fmtNumber(s.biggest_win)} • Worst loss: ${fmtNumber(s.biggest_loss)}`}
        />
      </div>
    );
  }, [state, load]);

  return (
    <PanelShell
      title={
        <span className="inline-flex items-center gap-2">
          <TrophyIcon className="w-5 h-5 opacity-70" />
          Player Stats
        </span>
      }
      right={<Button onClick={load}>Refresh</Button>}
    >
      {body}
    </PanelShell>
  );
}

/* ------------------------------ AccountPanel ------------------------------ */

type SettingsState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "loaded"; data: SettingsDto }
  | { kind: "error"; code?: number; message: string };

export function AccountPanel(props: any) {
  const [state, setState] = useState<SettingsState>({ kind: "idle" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const data = (await apiUserSettings()) as SettingsDto;
      setState({ kind: "loaded", data });
    } catch (err: any) {
      if (err?.code === 401) {
        setState({
          kind: "error",
          code: 401,
          message: "Authentication required. Open inside Telegram (or use dev fallback user_id=1).",
        });
      } else if (typeof err?.code === "number") {
        setState({ kind: "error", code: err.code, message: `Server error (HTTP ${err.code}).` });
      } else {
        setState({ kind: "error", message: "Network error while loading settings." });
      }
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const body = useMemo(() => {
    if (state.kind === "idle" || state.kind === "loading") {
      return <div className="flex items-center justify-center h-32 text-sm opacity-70">Loading settings…</div>;
    }
    if (state.kind === "error") {
      return (
        <div className="space-y-3">
          <div className="rounded-xl border border-red-300/40 bg-red-50/60 dark:bg-red-900/20 p-3 text-sm">
            <div className="font-medium">Couldn’t load account settings</div>
            <div className="opacity-80">
              {state.message}
              {state.code ? ` (code ${state.code})` : null}
            </div>
          </div>
          <Button onClick={load}>Try again</Button>
        </div>
      );
    }

    const s = state.data;

    return (
      <div className="grid grid-cols-1 gap-3">
        <div className="rounded-xl border border-black/10 dark:border-white/10 p-4 bg-black/[0.02] dark:bg-white/[0.04] space-y-3">
          <div className="flex items-center justify-between">
            <div className="font-medium flex items-center gap-2">
              <UserIcon className="w-5 h-5 opacity-70" />
              Profile
            </div>
            <span className="text-xs opacity-70">User ID: {s.user_id}</span>
          </div>
          <div className="text-sm opacity-80">Locale: {s.locale}</div>
        </div>

        <div className="rounded-xl border border-black/10 dark:border-white/10 p-4 bg-black/[0.02] dark:bg-white/[0.04] space-y-3">
          <div className="flex items-center justify-between">
            <div className="font-medium flex items-center gap-2">
              <SettingsIcon className="w-5 h-5 opacity-70" />
              Preferences
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="opacity-80">Theme</span>
              <span className="inline-flex items-center gap-2 px-2 py-1 rounded-lg border border-black/10 dark:border-white/10">
                {s.theme}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="opacity-80">Notifications</span>
              <span className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-black/10 dark:border-white/10">
                <BellIcon className="w-4 h-4 opacity-70" />
                {s.notifications ? "On" : "Off"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="opacity-80">Currency</span>
              <span className="inline-flex items-center gap-2 px-2 py-1 rounded-lg border border-black/10 dark:border-white/10">
                <WalletIcon className="w-4 h-4 opacity-70" />
                {s.currency}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="opacity-80">Experimental</span>
              <span className="inline-flex items-center gap-2 px-2 py-1 rounded-lg border border-black/10 dark:border-white/10">
                <ShieldIcon className="w-4 h-4 opacity-70" />
                {s.experimental ? "Enabled" : "Disabled"}
              </span>
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-black/10 dark:border-white/10 p-4 bg-black/[0.02] dark:bg-white/[0.04] space-y-2">
          <div className="font-medium flex items-center gap-2">
            <CoinsIcon className="w-5 h-5 opacity-70" />
            Tips
          </div>
          <ul className="text-sm opacity-80 list-disc pl-5 space-y-1">
            <li>Change theme in your device settings; the app follows Light/Dark when theme is set to <b>auto</b>.</li>
            <li>Open inside Telegram for full features and authentication.</li>
          </ul>
        </div>
      </div>
    );
  }, [state, load]);

  return (
    <PanelShell
      title={
        <span className="inline-flex items-center gap-2">
          <SettingsIcon className="w-5 h-5 opacity-70" />
          Account
        </span>
      }
      right={<Button onClick={load}>Refresh</Button>}
    >
      {body}
    </PanelShell>
  );
}
