// webapp-frontend/src/components/LobbyAndGame.tsx
import React, { useEffect, useMemo, useState } from "react";
import { apiJoinTable, apiTables, TableDto } from "../lib/api";
import {
  PlayIcon,
  LockIcon,
  UsersIcon,
  CoinsIcon,
  TrophyIcon,
} from "./icons";

type LobbyProps = {
  onOpenGame?: () => void;
};

type GameProps = {
  onBackToLobby?: () => void;
};

export function LobbyPanel(props: LobbyProps) {
  const [tables, setTables] = useState<TableDto[]>([]);
  const [loading, setLoading] = useState(true);
  const [joining, setJoining] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setLoading(true);
      const data = await apiTables();
      setTables(data);
      setError(null);
    } catch (e: any) {
      setError(e?.code === 401 ? "Sign in required" : (e?.message || "Failed to fetch tables"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleJoin(tableId: string) {
    try {
      setJoining(tableId);
      await apiJoinTable(tableId);
      setError(null);
      // After successful join, move to "Game"
      props.onOpenGame?.();
    } catch (e: any) {
      setError(e?.code === 401 ? "Sign in required" : (e?.message || "Failed to join"));
    } finally {
      setJoining(null);
    }
  }

  const sorted = useMemo(() => {
    return [...tables].sort((a, b) => {
      // running first, then waiting
      if (a.status !== b.status)
        return a.status === "running" ? -1 : 1;
      // more players first
      if (a.players_count !== b.players_count)
        return b.players_count - a.players_count;
      return a.name.localeCompare(b.name);
    });
  }, [tables]);

  return (
    <div className="card">
      <div className="h">
        <TrophyIcon width={18} height={18} />
        <span>Lobby</span>
        <span className="sub">{tables.length} tables</span>
      </div>

      <div className="hr" />

      {loading && <div className="pill">Loading tables…</div>}
      {error && <div className="pill" style={{ color: "var(--error)" }}>{error}</div>}
      {!loading && !error && (
        <div className="list" role="list">
          {sorted.map((t) => (
            <div key={t.id} className="item" role="listitem" aria-label={`${t.name} ${t.stakes}`}>
              <div>
                <h4>
                  {t.is_private && <LockIcon width={16} height={16} style={{ marginRight: 6, opacity: .8 }} />}
                  {t.name}
                </h4>
                <div className="meta">
                  <span title="stakes"><CoinsIcon width={14} height={14} style={{ marginRight: 4 }} /> {t.stakes}</span>
                  {" · "}
                  <span title="players"><UsersIcon width={14} height={14} style={{ marginRight: 4 }} /> {t.players_count}/{t.max_players}</span>
                  {" · "}
                  <span title="status" style={{ textTransform: "capitalize" }}>{t.status}</span>
                </div>
              </div>
              <button
                className="btn"
                onClick={() => handleJoin(t.id)}
                disabled={!!joining}
                aria-busy={joining === t.id}
              >
                <PlayIcon width={16} height={16} />
                {joining === t.id ? "Joining…" : "Join"}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function GamePanel(props: GameProps) {
  return (
    <div className="card">
      <div className="h">
        <TrophyIcon width={18} height={18} />
        <span>Game</span>
        <span className="sub">Texas Hold’em</span>
      </div>
      <div className="hr" />
      <div style={{ color: "var(--text-dim)" }}>
        You’ve joined a table. Your Telegram mini-app can render the table UI here.
      </div>
      <div style={{ marginTop: 10 }}>
        <button className="tab-btn" onClick={props.onBackToLobby}>
          Back to Lobby
        </button>
      </div>
    </div>
  );
}
