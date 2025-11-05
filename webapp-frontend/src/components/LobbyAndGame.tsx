// webapp-frontend/src/components/LobbyAndGame.tsx
import React, { useEffect, useMemo, useState } from "react";
import { apiTables, apiJoinTable, TableDto } from "../lib/api";
import {
  CardsIcon,
  LockIcon,
  PlayIcon,
  UsersIcon,
  CoinsIcon,
} from "./icons";

type LoadState<T> = { status: "idle" | "loading" | "ready" | "error"; data?: T; error?: any };

export function LobbyPanel() {
  const [state, setState] = useState<LoadState<TableDto[]>>({ status: "idle" });

  const refresh = async () => {
    setState({ status: "loading" });
    try {
      const rows = await apiTables();
      setState({ status: "ready", data: rows });
    } catch (e: any) {
      setState({ status: "error", error: e });
    }
  };

  useEffect(() => { refresh(); }, []);

  const rows = state.data ?? [];

  const body = (
    <div className="stack">
      {state.status === "error" && (
        <div className={`banner ${state.error?.code === 404 ? "" : "error"}`}>
          {state.error?.code === 404
            ? <>Server tables endpoint not found — using local mock data. Implement: <code>/api/tables</code></>
            : <>Couldn’t load tables ({String(state.error?.code || "")}).</>}
        </div>
      )}

      <div className="card stack">
        <div className="section-title"><CardsIcon /> Tables Lobby</div>
        <button className="btn ghost" onClick={refresh}>↻ Refresh</button>

        <div className="list">
          {rows.map((t) => (
            <div key={t.id} className="list-item">
              <div className="kv">
                <div className="value">{t.name}</div>
                <div className="small">
                  {t.is_private ? <>Private <LockIcon /></> : "Public"} · {t.status === "running" ? "Running" : "Waiting"}
                </div>
                <div className="small">
                  <CoinsIcon style={{verticalAlign:"-2px"}}/> {t.stakes} · <UsersIcon style={{verticalAlign:"-2px"}}/> {t.players_count}/{t.max_players}
                </div>
                <div className="small">ID: {t.id}</div>
              </div>
              <div>
                {t.status === "running" ? (
                  <button className="btn" onClick={() => alert("Spectate stub")}>▶ Spectate</button>
                ) : (
                  <button className="btn primary" onClick={() => join(t.id)}>Join</button>
                )}
              </div>
            </div>
          ))}
          {rows.length === 0 && <div className="small">No tables available.</div>}
        </div>
      </div>

      <div className="card stack">
        <div className="section-title"><LockIcon /> Create a table</div>
        <div className="grid-2">
          <input className="list-item" placeholder="Friends Table" />
          <select className="list-item"><option>1 BB</option><option>2 BB</option><option>5 BB</option></select>
          <select className="list-item"><option>6-max</option><option>9-max</option></select>
          <label className="list-item" style={{gap:8, alignItems:"center"}}>
            <input type="checkbox" defaultChecked /> Private (invite-only)
          </label>
        </div>
        <button className="btn primary">Create table</button>
        <div className="small">This is a UI shell; wire to your backend when ready.</div>
      </div>
    </div>
  );

  async function join(id: string) {
    try {
      await apiJoinTable(id);
      alert(`Joined ${id} (demo)`);
    } catch (e: any) {
      if (e?.code === 401) alert("Authentication required. Open inside Telegram or enable dev fallback.");
      else alert(`Join failed (${e?.code || "error"})`);
    }
  }

  return body;
}

export function GamePanel() {
  return (
    <div className="stack">
      <div className="card">
        <div className="section-title"><PlayIcon /> Game</div>
        <p className="small" style={{lineHeight:1.6}}>
          Game surface coming here. Your core Telegram-bot engine runs gameplay; this panel
          is a mini-app surface for table HUD, actions, and stats. Hook this to your real-time
          channel when ready.
        </p>
      </div>
    </div>
  );
}
