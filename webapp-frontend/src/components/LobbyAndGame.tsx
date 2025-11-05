// webapp-frontend/src/components/LobbyAndGame.tsx
import React, { useEffect, useMemo, useState } from "react";
import { apiJoinTable, apiTables, TableDto } from "../lib/api";
import { CardsIcon, LockIcon, PlayIcon, UsersIcon } from "./icons";

type FetchState<T> =
  | { status: "idle" | "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; code?: number; message?: string };

export function LobbyPanel() {
  const [query, setQuery] = useState("");
  const [joining, setJoining] = useState<string | null>(null);
  const [state, setState] = useState<FetchState<TableDto[]>>({ status: "loading" });

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const tables = await apiTables();
        if (!alive) return;
        setState({ status: "ready", data: tables });
      } catch (e: any) {
        if (!alive) return;
        setState({ status: "error", code: e?.code, message: e?.message || String(e) });
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const filtered = useMemo(() => {
    if (state.status !== "ready") return [];
    const q = query.trim().toLowerCase();
    if (!q) return state.data;
    return state.data.filter((t) => {
      const vis = t.is_private ? "private" : "public";
      const s = `${t.name} ${t.stakes} ${vis} ${t.players_count}/${t.max_players} ${t.status}`.toLowerCase();
      return s.includes(q);
    });
  }, [state, query]);

  async function join(tableId: string) {
    try {
      setJoining(tableId);
      await apiJoinTable(tableId);
      // In a real app: navigate to Game tab or open table HUD
      alert(`Joined ${tableId} (server acknowledged).`);
    } catch (e: any) {
      if (e?.code === 401) alert("Please open the app inside Telegram to authenticate.");
      else if (e?.code === 404) alert("Join endpoint not found on server.");
      else alert(`Join failed: ${e?.message || e}`);
    } finally {
      setJoining(null);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <div className="title">
          <CardsIcon className="icon" /> Tables Lobby
        </div>
      </div>

      <input
        className="input"
        placeholder="Search by name, stake, visibility…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      {state.status === "loading" && <div className="card muted">Loading tables…</div>}
      {state.status === "error" && (
        <div className="alert warn">
          {state.code === 404
            ? "Server tables endpoint not found — implement /api/tables on your backend."
            : "Could not load tables. Check network/server logs."}
        </div>
      )}

      {state.status === "ready" && (
        <ul className="list">
          {filtered.map((t) => {
            const vis = t.is_private ? (
              <>
                <LockIcon className="icon sm" /> Private
              </>
            ) : (
              "Public"
            );

            return (
              <li key={t.id} className="row">
                <div className="row-main">
                  <div className="row-title">{t.name}</div>
                  <div className="row-sub">
                    <span>{vis}</span>
                    <span>{t.status[0].toUpperCase() + t.status.slice(1)}</span>
                    <span>
                      💰 {t.stakes}
                    </span>
                    <span>
                      <UsersIcon className="icon sm" /> {t.players_count}/{t.max_players}
                    </span>
                    <span className="chip">ID: {t.id}</span>
                  </div>
                </div>
                <div className="row-cta">
                  <button
                    className="btn"
                    disabled={joining === t.id}
                    onClick={() => join(t.id)}
                    title="Join table"
                  >
                    <PlayIcon className="icon sm" />
                    {joining === t.id ? "Joining…" : "Join"}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function GamePanel() {
  return (
    <div className="panel">
      <div className="panel-header">
        <div className="title">
          <PlayIcon className="icon" /> Game
        </div>
      </div>

      <div className="card">
        This is the mini-app game surface. When your real-time channel is ready, mount your table HUD
        and actions here (spectators, hand history, timers, etc).
      </div>
    </div>
  );
}
