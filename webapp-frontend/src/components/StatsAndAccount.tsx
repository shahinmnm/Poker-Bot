// webapp-frontend/src/components/StatsAndAccount.tsx
import React, { useEffect, useState } from "react";
import { apiUserSettings, apiUserStats } from "../lib/api";
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
  CoinsIcon,
  TrophyIcon,
  UserIcon,
} from "./icons";

type LoadState<T> = { status: "idle" | "loading" | "ready" | "error"; data?: T; error?: any };

export function StatsPanel() {
  const [state, setState] = useState<LoadState<any>>({ status: "idle" });

  const refresh = async () => {
    setState({ status: "loading" });
    try {
      const stats = await apiUserStats();
      setState({ status: "ready", data: stats });
    } catch (e: any) {
      setState({ status: "error", error: e });
    }
  };

  useEffect(() => { refresh(); }, []);

  const s = state.data || {
    hands_played: 0,
    biggest_win: 0,
    biggest_loss: 0,
    win_rate: 0,
    last_played: "",
    streak_days: 0,
    chip_balance: 0,
    rank: "-",
  };

  return (
    <div className="stack">
      {state.status === "error" && (
        <div className={`banner ${state.error?.code === 404 ? "" : "error"}`}>
          {state.error?.code === 404
            ? <>Server stats endpoint not found — showing local defaults. Once your backend exposes <code>/api/user/stats</code>, this panel will auto-populate.</>
            : state.error?.code === 401
              ? <>Authentication required. Open inside Telegram or enable dev user fallback.</>
              : <>Couldn’t load stats ({String(state.error?.code || "")}).</>}
        </div>
      )}

      <div className="card stack">
        <div className="section-title"><TrophyIcon /> Career Stats</div>
        <button className="btn ghost" onClick={refresh}>↻ Refresh</button>

        <div className="grid-2">
          <div className="list-item"><div className="kv"><div className="label">Rank</div><div className="value">{s.rank}</div></div><BadgeCheckIcon /></div>
          <div className="list-item"><div className="kv"><div className="label">Current streak</div><div className="value">{s.streak_days} <FlameIcon style={{verticalAlign:"-2px"}}/></div></div></div>

          <div className="list-item"><div className="kv"><div className="label">Hands played</div><div className="value">{s.hands_played}</div></div></div>
          <div className="list-item"><div className="kv"><div className="label">Win rate</div><div className="value">{Math.round((s.win_rate || 0) * 100)}%</div></div><ChartIcon /></div>

          <div className="list-item"><div className="kv"><div className="label">Chip balance</div><div className="value chips">{s.chip_balance.toLocaleString()}</div></div><CoinsIcon /></div>
          <div className="list-item"><div className="kv"><div className="label">Best / Worst</div><div className="value">{s.biggest_win.toLocaleString()} / {s.biggest_loss.toLocaleString()}</div></div></div>
        </div>

        <div className="small">Last played: {s.last_played ? new Date(s.last_played).toLocaleString() : "—"}</div>
      </div>
    </div>
  );
}

export function AccountPanel() {
  const [state, setState] = useState<LoadState<any>>({ status: "idle" });

  const refresh = async () => {
    setState({ status: "loading" });
    try {
      const settings = await apiUserSettings();
      setState({ status: "ready", data: settings });
    } catch (e: any) {
      setState({ status: "error", error: e });
    }
  };

  useEffect(() => { refresh(); }, []);

  const st = state.data || { user_id: 0, theme: "auto", notifications: true, locale: "en", currency: "chips", experimental: false };

  return (
    <div className="stack">
      {state.status === "error" && (
        <div className={`banner ${state.error?.code === 404 ? "" : "error"}`}>
          {state.error?.code === 404
            ? <>Server settings endpoint not found — using local defaults. When you add <code>/api/user/settings</code> this will sync automatically.</>
            : state.error?.code === 401
              ? <>Authentication required. Open inside Telegram or enable dev user fallback.</>
              : <>Couldn’t load settings ({String(state.error?.code || "")}).</>}
        </div>
      )}

      <div className="card stack">
        <div className="section-title"><UserIcon /> Account & Settings</div>
        <button className="btn ghost" onClick={refresh}>↻ Refresh</button>

        <div className="grid-2">
          <div className="list-item">
            <div className="kv">
              <div className="label">User ID</div>
              <div className="value">{st.user_id}</div>
            </div>
          </div>
          <div className="list-item">
            <div className="kv">
              <div className="label">Locale</div>
              <div className="value"><GlobeIcon style={{verticalAlign:"-2px"}}/> {st.locale}</div>
            </div>
          </div>

          <div className="list-item">
            <div className="kv">
              <div className="label">Theme</div>
              <div className="value">{st.theme}</div>
            </div>
            <CogIcon />
          </div>

          <div className="list-item">
            <div className="kv">
              <div className="label">Notifications</div>
              <div className="value">{st.notifications ? "On" : "Off"}</div>
            </div>
            <BellIcon />
          </div>

          <div className="list-item">
            <div className="kv">
              <div className="label">Currency</div>
              <div className="value">{st.currency}</div>
            </div>
            <WalletIcon />
          </div>

          <div className="list-item">
            <div className="kv">
              <div className="label">Experimental</div>
              <div className="value">{st.experimental ? "Enabled" : "Disabled"}</div>
            </div>
            <ShieldIcon />
          </div>
        </div>

        <hr className="sep" />
        <div className="small">
          • Change theme in your device settings; the app follows Light/Dark when theme is set to <b>auto</b>.<br/>
          • Open inside Telegram for full features and authentication.
        </div>
      </div>
    </div>
  );
}
