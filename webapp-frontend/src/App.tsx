// webapp-frontend/src/App.tsx
import React, { useMemo, useState } from "react";
import { LobbyPanel, GamePanel } from "./components/LobbyAndGame";
import { StatsPanel, AccountPanel } from "./components/StatsAndAccount";
import "./styles/theme.css";

type TabKey = "lobby" | "game" | "stats" | "account";

type Tab = {
  key: TabKey;
  label: string;
  icon?: React.ReactNode;
};

export default function App() {
  const [tab, setTab] = useState<TabKey>("lobby");

  const tabs: Tab[] = useMemo(
    () => [
      { key: "lobby", label: "Lobby" },
      { key: "game", label: "Game" },
      { key: "stats", label: "Stats" },
      { key: "account", label: "Account" },
    ],
    []
  );

  return (
    <div className="app-shell">
      {/* Swipeable top tab row */}
      <div className="top-tabs" role="tablist" aria-label="Sections">
        {tabs.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            data-active={tab === t.key}
            className="tab-btn"
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Panels */}
      {tab === "lobby" && <LobbyPanel onOpenGame={() => setTab("game")} />}
      {tab === "game" && <GamePanel onBackToLobby={() => setTab("lobby")} />}
      {tab === "stats" && <StatsPanel />}
      {tab === "account" && <AccountPanel />}
    </div>
  );
}
