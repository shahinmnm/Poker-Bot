// webapp-frontend/src/App.tsx
import React, { useEffect, useMemo, useState } from "react";
import { LobbyPanel, GamePanel } from "./components/LobbyAndGame";
import { StatsPanel, AccountPanel } from "./components/StatsAndAccount";
import { CardsIcon, LayoutIcon, TrophyIcon, UserIcon } from "./components/icons";
import {
  detectTelegramColorScheme,
  getTelegramInitData,
  watchTelegramTheme,
} from "./lib/api";
import "./app.css";

type TabKey = "lobby" | "game" | "stats" | "account";

const TABS: { key: TabKey; label: string; icon: React.ReactNode }[] = [
  { key: "lobby", label: "Lobby", icon: <LayoutIcon className="icon" /> },
  { key: "game", label: "Game", icon: <CardsIcon className="icon" /> },
  { key: "stats", label: "Stats", icon: <TrophyIcon className="icon" /> },
  { key: "account", label: "Account", icon: <UserIcon className="icon" /> },
];

export default function App() {
  const [tab, setTab] = useState<TabKey>("lobby");

  // Sync Telegram theme to CSS vars for readable dark/light text
  useEffect(() => {
    const root = document.documentElement;
    function apply(s: "light" | "dark" | "auto") {
      root.setAttribute("data-tg", s === "auto" ? "" : s);
    }
    apply(detectTelegramColorScheme());
    const off = watchTelegramTheme(apply);
    return () => off();
  }, []);

  // Helpful banner in dev if not in Telegram
  const devInfo = useMemo(() => {
    if (getTelegramInitData()) return null;
    return "Running outside Telegram — using dev user_id=1 for API.";
  }, []);

  return (
    <div className="app">
      <div className="topbar">
        <div className="tabs scroller">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={`tab ${tab === t.key ? "active" : ""}`}
              onClick={() => setTab(t.key)}
            >
              {t.icon}
              <span className="tab-label">{t.label}</span>
            </button>
          ))}
        </div>
      </div>

      <main className="content">
        {tab === "lobby" && <LobbyPanel />}
        {tab === "game" && <GamePanel />}
        {tab === "stats" && <StatsPanel />}
        {tab === "account" && <AccountPanel />}
      </main>

      <footer className="footer">
        {devInfo ? devInfo : "Telegram WebApp ready"}
      </footer>
    </div>
  );
}
