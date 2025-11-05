// webapp-frontend/src/App.tsx
import React, { useEffect, useMemo, useState } from "react";
import { LobbyPanel, GamePanel } from "./components/LobbyAndGame";
import { StatsPanel, AccountPanel } from "./components/StatsAndAccount";
import { CardsIcon, LayoutIcon, TrophyIcon, UserIcon } from "./components/icons";
import { getTelegramInitData } from "./lib/api";
import "./app.css";

/**
 * Root app with swipeable top tabs and panels.
 * - Tabs are horizontally scrollable (swipe on mobile).
 * - Theme reacts to device mode (prefers-color-scheme) and Telegram theme.
 */
type TabKey = "lobby" | "game" | "stats" | "account";

function useTelegramTheme() {
  const [theme, setTheme] = useState<"light" | "dark" | "auto">("auto");
  useEffect(() => {
    // @ts-ignore
    const tg = (window as any)?.Telegram?.WebApp;
    if (!tg) return;
    try {
      const scheme = tg.colorScheme as "light" | "dark" | undefined;
      if (scheme) {
        document.documentElement.dataset.tg = scheme;
        setTheme("auto"); // let CSS pick via [data-tg]
      }
      tg.onEvent?.("themeChanged", () => {
        const sc = tg.colorScheme as "light" | "dark" | undefined;
        if (sc) document.documentElement.dataset.tg = sc;
      });
      tg.ready?.();
    } catch {}
  }, []);
  return theme;
}

export default function App() {
  const [tab, setTab] = useState<TabKey>("lobby");
  const [currentTableId, setCurrentTableId] = useState<string | null>(null);
  useTelegramTheme();

  // Show a tiny footer about Telegram presence/auth
  const shellNote = useMemo(() => {
    const inTg = !!getTelegramInitData();
    return inTg
      ? `Telegram WebApp ready`
      : `Running standalone (dev). Open inside Telegram for auth.`;
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="tabs scroller" role="tablist" aria-label="Main sections">
          <TabButton
            active={tab === "lobby"}
            onClick={() => setTab("lobby")}
            icon={<CardsIcon className="icon" />}
            label="Lobby"
          />
          <TabButton
            active={tab === "game"}
            onClick={() => setTab("game")}
            icon={<LayoutIcon className="icon" />}
            label="Game"
          />
          <TabButton
            active={tab === "stats"}
            onClick={() => setTab("stats")}
            icon={<TrophyIcon className="icon" />}
            label="Stats"
          />
          <TabButton
            active={tab === "account"}
            onClick={() => setTab("account")}
            icon={<UserIcon className="icon" />}
            label="Account"
          />
        </div>
      </header>

      <main className="content">
        {tab === "lobby" && (
          <LobbyPanel
            onJoinSuccess={(tableId) => {
              setCurrentTableId(tableId);
              setTab("game");
            }}
          />
        )}
        {tab === "game" && <GamePanel currentTableId={currentTableId} />}
        {tab === "stats" && <StatsPanel />}
        {tab === "account" && <AccountPanel />}
      </main>

      <footer className="footer">{shellNote}</footer>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick(): void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      className={`tab ${active ? "active" : ""}`}
      onClick={onClick}
      role="tab"
      aria-selected={active}
    >
      {icon}
      <span className="tab-label">{label}</span>
    </button>
  );
}
