import React, { useEffect, useMemo, useState } from "react";
import {
  BadgeCheckIcon,
  BellIcon,
  ChartIcon,
  CogIcon,
  FlameIcon,
  GlobeIcon,
  PlusIcon,
  ShieldIcon,
  WalletIcon,
} from "./icons";

// -------------------------------------------------------------
// Stats + Account settings panel for Poker WebApp.
// Fetches:
//   - GET /api/user/settings
//   - GET /api/user/stats
// (Dev fallback adds ?user_id=1 outside Telegram.)
// -------------------------------------------------------------

type SettingsDto = {
  user_id: number;
  theme: "auto" | "dark" | "light";
  notifications: boolean;
  locale: string;
  currency: "chips" | "usd";
  experimental: boolean;
};

type StatsDto = {
  user_id: number;
  hands_played: number;
  biggest_win: number;
  biggest_loss: number; // negative
  win_rate: number; // 0..1
  last_played: string; // ISO
  streak_days: number;
  chip_balance: number;
  rank: string;
};

type FetchResult<T> = { ok: boolean; data?: T; status: number };

// Detect Telegram WebApp context
function getTelegramInitData(): string | null {
  try {
    // @ts-ignore
    const tg = (window as any)?.Telegram?.WebApp;
    if (tg && typeof tg.initData === "string" && tg.initData.length > 0) {
      return tg.initData;
    }
  } catch {}
  return null;
}

// Centralized fetch helper with dev identity fallback.
// - Append ?user_id=1 when not inside Telegram
// - Include Telegram init header when present
async function safeFetch<T>(
  path: string,
  opts: RequestInit,
  token?: string | null
): Promise<{ ok: boolean; data?: T; status: number }> {
  try {
    const base = typeof window !== "undefined" ? window.location.origin : "";
    const url = new URL(path, base);

    const isTelegram = !!(window as any)?.Telegram?.WebApp?.initData;
    if (!isTelegram && !url.searchParams.has("user_id")) {
      url.searchParams.set("user_id", "1");
    }

    const headers = new Headers(opts.headers as any);
    if (token) headers.set("X-Telegram-Init-Data", token);

    const res = await fetch(url.toString(), {
      ...opts,
      credentials: "include",
      headers,
    });

    const ct = res.headers.get("content-type") || "";
    const isJson = /application\/json/i.test(ct);
    const data = isJson ? await res.json().catch(() => ({})) : ({} as any);

    return { ok: res.ok, data, status: res.status };
  } catch (e) {
    console.warn("safeFetch failed", e);
    return { ok: false, status: 0 };
  }
}

export default function StatsAndAccount() {
  const initData = useMemo(getTelegramInitData, []);
  const [settings, setSettings] = useState<SettingsDto | null>(null);
  const [stats, setStats] = useState<StatsDto | null>(null);

  const [serverAvailable, setServerAvailable] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);

  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setLoading(true);
      setMessage(null);

      const [settingsRes, statsRes] = await Promise.all([
        safeFetch<SettingsDto>("/api/user/settings", { method: "GET" }, initData),
        safeFetch<StatsDto>("/api/user/stats", { method: "GET" }, initData),
      ]);

      if (cancelled) return;

      if (settingsRes.ok) setSettings(settingsRes.data as SettingsDto);
      if (statsRes.ok) setStats(statsRes.data as StatsDto);

      // If either is 404, show "endpoint missing" banner.
      // Otherwise, if both are OK, mark serverAvailable = true.
      if (settingsRes.ok && statsRes.ok) {
        setServerAvailable(true);
      } else if (settingsRes.status === 404 || statsRes.status === 404) {
        setServerAvailable(false);
      } else {
        setServerAvailable(null);
      }

      setLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [initData]);

  function fmtPct(p: number) {
    const clamped = Math.max(0, Math.min(1, p || 0));
    return `${(clamped * 100).toFixed(1)}%`;
  }

  function fmtChips(n: number) {
    return `${n.toLocaleString()} chips`;
  }

  async function claimDailyBonus() {
    setMessage(null);
    const res = await safeFetch<{ ok?: boolean; message?: string }>(
      "/api/user/bonus",
      { method: "POST" },
      initData
    );
    if (res.ok) {
      setMessage("Daily bonus claimed!");
    } else if (res.status === 404) {
      setMessage("Bonus endpoint not implemented yet.");
    } else if (res.status === 401) {
      setMessage("Unauthorized (401). In Telegram the identity is automatic; dev mode attaches user_id=1.");
    } else {
      setMessage(`Failed to claim bonus (HTTP ${res.status || "ERR"}).`);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <header className="flex items-center justify-between">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <ChartIcon className="w-5 h-5" /> Your Stats
        </h2>
        {serverAvailable === false && (
          <span className="text-xs text-amber-500">
            Server settings/stats endpoints not found — using defaults. Add /api/user/settings and /api/user/stats.
          </span>
        )}
      </header>

      {loading ? (
        <div className="text-sm opacity-70">Loading…</div>
      ) : (
        <>
          <section className="grid gap-3 md:grid-cols-2">
            <div className="rounded-xl border border-white/10 p-4 bg-white/5">
              <div className="flex items-center gap-2 text-sm opacity-70">
                <WalletIcon className="w-4 h-4" />
                Balance
              </div>
              <div className="mt-1 text-2xl font-semibold">
                {stats ? fmtChips(stats.chip_balance) : "—"}
              </div>

              <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-lg p-3 bg-white/5 border border-white/10">
                  <div className="opacity-70">Hands played</div>
                  <div className="font-medium">{stats?.hands_played ?? "—"}</div>
                </div>
                <div className="rounded-lg p-3 bg-white/5 border border-white/10">
                  <div className="opacity-70">Win rate</div>
                  <div className="font-medium">{stats ? fmtPct(stats.win_rate) : "—"}</div>
                </div>
                <div className="rounded-lg p-3 bg-white/5 border border-white/10">
                  <div className="opacity-70">Biggest win</div>
                  <div className="font-medium">
                    {stats ? fmtChips(stats.biggest_win) : "—"}
                  </div>
                </div>
                <div className="rounded-lg p-3 bg-white/5 border border-white/10">
                  <div className="opacity-70">Biggest loss</div>
                  <div className="font-medium">
                    {stats ? fmtChips(stats.biggest_loss) : "—"}
                  </div>
                </div>
              </div>

              {stats?.rank && (
                <div className="mt-3 inline-flex items-center gap-2 text-xs rounded-lg px-2 py-1 border border-white/20">
                  <BadgeCheckIcon className="w-4 h-4" />
                  Rank: {stats.rank}
                </div>
              )}
            </div>

            <div className="rounded-xl border border-white/10 p-4 bg-white/5">
              <div className="flex items-center gap-2 text-sm opacity-70">
                <CogIcon className="w-4 h-4" />
                Account Settings
              </div>

              <div className="mt-3 grid gap-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="opacity-70">Theme</span>
                  <span className="font-medium">{settings?.theme ?? "auto"}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="opacity-70 flex items-center gap-1">
                    <BellIcon className="w-4 h-4" /> Notifications
                  </span>
                  <span className="font-medium">{settings?.notifications ? "On" : "Off"}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="opacity-70 flex items-center gap-1">
                    <GlobeIcon className="w-4 h-4" /> Locale
                  </span>
                  <span className="font-medium">{settings?.locale ?? "en"}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="opacity-70 flex items-center gap-1">
                    <ShieldIcon className="w-4 h-4" /> Experimental
                  </span>
                  <span className="font-medium">{settings?.experimental ? "Enabled" : "Disabled"}</span>
                </div>
              </div>

              <div className="mt-4 flex items-center justify-between">
                <button
                  onClick={claimDailyBonus}
                  className="inline-flex items-center gap-2 rounded-lg border border-white/20 px-3 py-2"
                >
                  <PlusIcon className="w-4 h-4" />
                  Claim Daily Bonus
                </button>
                <div className="text-xs opacity-70 flex items-center gap-1">
                  <FlameIcon className="w-4 h-4" />
                  Streak: {stats?.streak_days ?? 0} days
                </div>
              </div>
            </div>
          </section>

          {message && (
            <div className="rounded-lg p-3 bg-emerald-500/10 border border-emerald-500/30 text-emerald-200 text-sm">
              {message}
            </div>
          )}
        </>
      )}
    </div>
  );
}
