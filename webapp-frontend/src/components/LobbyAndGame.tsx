// webapp-frontend/src/components/LobbyAndGame.tsx
import React, { useEffect, useMemo, useState } from "react";
import { apiJoinTable, apiTables, TableDto } from "../lib/api";
import {
  ArrowRightIcon,
  CardsIcon,
  CoinsIcon,
  LockIcon,
  PlayIcon,
  UsersIcon,
} from "./icons";

type LobbyProps = {
  onJoinSuccess(tableId: string): void;
};

export const LobbyPanel: React.FC<LobbyProps> = ({ onJoinSuccess }) => {
  const [loading, setLoading] = useState(true);
  const [tables, setTables] = useState<TableDto[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const data = await apiTables();
      setTables(data);
    } catch (e: any) {
      setError(e?.message || "Failed to load tables");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return tables;
    return tables.filter((t) =>
      [t.name, t.stakes, t.is_private ? "private" : "public", t.status]
        .join(" ")
        .toLowerCase()
        .includes(q)
    );
  }, [tables, filter]);

  async function handleJoin(t: TableDto) {
    try {
      setError(null);
      await apiJoinTable(t.id);
      onJoinSuccess(t.id);
    } catch (e: any) {
      if (e?.code === 401) {
        setError(
          "Sign in inside Telegram to join tables (AUTH_REQUIRED). Open this WebApp from your bot."
        );
      } else {
        setError(`Join failed: ${e?.message || "unknown error"}`);
      }
    }
  }

  return (
    <section className="panel">
      <header className="panel-header">
        <div className="title">
          <CardsIcon className="icon" /> Tables Lobby
        </div>
        <button className="btn ghost" onClick={refresh} disabled={loading}>
          ↻ Refresh
        </button>
      </header>

      <div className="card">
        <input
          className="input"
          placeholder="Search by name, stake, visibility…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          inputMode="search"
        />
      </div>

      {error && <div className="alert warn">{error}</div>}

      {loading ? (
        <div className="muted">Loading tables…</div>
      ) : visible.length === 0 ? (
        <div className="muted">No tables match your search.</div>
      ) : (
        <ul className="list">
          {visible.map((t) => (
            <li key={t.id} className="row">
              <div className="row-main">
                <div className="row-title">
                  {t.name}{" "}
                  {t.is_private ? (
                    <span title="Private table">
                      <LockIcon className="icon sm" />
                    </span>
                  ) : null}
                </div>
                <div className="row-sub">
                  <CoinsIcon className="icon sm" /> {t.stakes} &nbsp;·&nbsp;
                  <UsersIcon className="icon sm" /> {t.players_count}/{t.max_players}
                  &nbsp;·&nbsp; {t.is_private ? "Private" : "Public"}
                  &nbsp;·&nbsp; {t.status === "running" ? "Running" : "Waiting"}
                  &nbsp;·&nbsp; ID: {t.id}
                </div>
              </div>
              <div className="row-cta">
                <button
                  className="btn"
                  onClick={() => handleJoin(t)}
                  disabled={t.is_private || t.status === "running"}
                  title={
                    t.is_private
                      ? "Invite-only"
                      : t.status === "running"
                      ? "Game is already running"
                      : "Join this table"
                  }
                >
                  <PlayIcon className="icon" />
                  Join
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

type GameProps = {
  currentTableId?: string | null;
};

export const GamePanel: React.FC<GameProps> = ({ currentTableId }) => {
  return (
    <section className="panel">
      <header className="panel-header">
        <div className="title">
          <PlayIcon className="icon" /> Game
        </div>
        {currentTableId ? (
          <div className="chip">
            Table <ArrowRightIcon className="icon sm" /> {currentTableId}
          </div>
        ) : (
          <div className="muted">Join a table from the Lobby to start.</div>
        )}
      </header>

      <div className="card">
        <p className="muted">
          Game surface placeholder. Render your real-time table HUD/actions here and
          attach to your channel (WebSocket/long-poll/Telegram callbacks).
        </p>
      </div>
    </section>
  );
};
