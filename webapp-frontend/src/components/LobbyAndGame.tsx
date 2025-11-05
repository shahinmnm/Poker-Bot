// webapp-frontend/src/components/LobbyAndGame.tsx
//
// Panels for Lobby (table list + join) and in-game placeholder.
// - Exports *named* LobbyPanel and GamePanel to satisfy `import { LobbyPanel, GamePanel } ...` in App.tsx
// - Fetches tables via apiTables()
// - Join via apiJoinTable(tableId)
// - Handles 401 distinctly (AUTH_REQUIRED thrown by api.ts), 404/other HTTP_xx with friendly UI
// - Zero external deps, uses icons from ./icons.tsx

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiJoinTable, apiTables, TableDto } from "../lib/api";
import {
  ArrowRightIcon,
  CoinsIcon,
  LockIcon,
  PlayIcon,
  UsersIcon,
  CardsIcon,
  LayoutIcon,
} from "./icons";

// ---------- Shared UI bits ----------

function PanelShell(props: { title: React.ReactNode; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="w-full max-w-3xl mx-auto rounded-2xl border border-black/10 dark:border-white/10 bg-white/60 dark:bg-black/40 backdrop-blur p-4 md:p-6 shadow-sm">
      <header className="flex items-center justify-between gap-3 mb-4">
        <h2 className="text-base md:text-lg font-semibold flex items-center gap-2">
          <LayoutIcon className="w-5 h-5 opacity-70" />
          {props.title}
        </h2>
        {props.right}
      </header>
      <div>{props.children}</div>
    </section>
  );
}

function Pill(props: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs border border-black/10 dark:border-white/15 bg-black/[0.03] dark:bg-white/[0.06]">
      {props.children}
    </span>
  );
}

function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement> & { left?: React.ReactNode }) {
  const { className = "", left, children, ...rest } = props;
  return (
    <button
      {...rest}
      className={
        "inline-flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium border transition " +
        "border-black/10 dark:border-white/15 bg-black/[0.04] dark:bg-white/[0.06] " +
        "hover:bg-black/[0.06] dark:hover:bg-white/[0.10] active:translate-y-[0.5px] " +
        "disabled:opacity-60 disabled:cursor-not-allowed " +
        className
      }
    >
      {left}
      {children}
    </button>
  );
}

// ---------- LobbyPanel ----------

type LobbyState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "loaded"; tables: TableDto[] }
  | { kind: "error"; code?: number; message: string };

export function LobbyPanel(props: any) {
  // Accept any props to be compatible with existing App.tsx usage.
  // If parent provides onJoin, we call it after a successful join.
  const onJoin: ((t: TableDto) => void) | undefined = props?.onJoin;

  const [state, setState] = useState<LobbyState>({ kind: "idle" });
  const [joiningId, setJoiningId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const tables = await apiTables();
      setState({ kind: "loaded", tables });
    } catch (err: any) {
      if (err?.code === 401) {
        // AUTH_REQUIRED is already mapped in api.ts to code 401
        setState({
          kind: "error",
          code: 401,
          message:
            "Authentication required. Open the mini app inside Telegram, or use the dev fallback (user_id=1).",
        });
      } else if (typeof err?.code === "number") {
        setState({
          kind: "error",
          code: err.code,
          message: `Server error (HTTP ${err.code}).`,
        });
      } else {
        setState({ kind: "error", message: "Network error while loading tables." });
      }
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const canJoin = useCallback((t: TableDto) => {
    const full = t.players_count >= t.max_players;
    const running = t.status === "running";
    return !full && !running;
  }, []);

  const join = useCallback(
    async (t: TableDto) => {
      if (!canJoin(t) || joiningId) return;
      setJoiningId(t.id);
      try {
        await apiJoinTable(t.id);
        // Optional callback to parent (if App.tsx provided one)
        onJoin?.(t);
        // Light feedback
        setJoiningId(null);
      } catch (err: any) {
        setJoiningId(null);
        if (err?.code === 401) {
          alert("You need to open this app inside Telegram to join tables (or use dev user_id=1).");
        } else if (err?.code === 404) {
          alert("Join endpoint not found on server. Ensure /tables/{id}/join is routed (with /api prefix too).");
        } else {
          alert("Join failed. Please try again.");
        }
      }
    },
    [joiningId, onJoin, canJoin]
  );

  const content = useMemo(() => {
    if (state.kind === "loading" || state.kind === "idle") {
      return (
        <div className="flex items-center justify-center h-32 text-sm opacity-70">
          Loading tables…
        </div>
      );
    }
    if (state.kind === "error") {
      return (
        <div className="space-y-3">
          <div className="rounded-xl border border-red-300/40 bg-red-50/60 dark:bg-red-900/20 p-3 text-sm">
            <div className="font-medium">Couldn’t load tables</div>
            <div className="opacity-80">
              {state.message}
              {state.code ? ` (code ${state.code})` : null}
            </div>
          </div>
          <Button onClick={load} left={<ArrowRightIcon className="w-4 h-4" />}>
            Try again
          </Button>
        </div>
      );
    }
    // loaded
    if (state.tables.length === 0) {
      return (
        <div className="flex items-center justify-center h-32 text-sm opacity-70">
          No public tables available right now.
        </div>
      );
    }
    return (
      <ul className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {state.tables.map((t) => {
          const full = t.players_count >= t.max_players;
          const running = t.status === "running";
          const disabled = !canJoin(t) || joiningId === t.id;

          return (
            <li
              key={t.id}
              className="rounded-2xl border border-black/10 dark:border-white/10 p-4 bg-black/[0.02] dark:bg-white/[0.04] flex flex-col gap-3"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="font-semibold truncate">{t.name}</div>
                <div className="flex items-center gap-2">
                  {t.is_private ? (
                    <Pill>
                      <LockIcon className="w-3.5 h-3.5 mr-1" />
                      Private
                    </Pill>
                  ) : (
                    <Pill>Public</Pill>
                  )}
                  {running ? <Pill>Running</Pill> : <Pill>Waiting</Pill>}
                </div>
              </div>

              <div className="text-sm flex items-center gap-3 flex-wrap">
                <span className="inline-flex items-center gap-1 opacity-80">
                  <CoinsIcon className="w-4 h-4" />
                  {t.stakes}
                </span>
                <span className="inline-flex items-center gap-1 opacity-80">
                  <UsersIcon className="w-4 h-4" />
                  {t.players_count}/{t.max_players}
                </span>
              </div>

              <div className="flex items-center justify-between gap-2">
                <div className="text-xs opacity-70">ID: {t.id}</div>
                <Button
                  onClick={() => join(t)}
                  disabled={disabled}
                  left={<PlayIcon className="w-4 h-4" />}
                >
                  {joiningId === t.id ? "Joining…" : running ? "Spectate" : full ? "Full" : "Join"}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    );
  }, [state, canJoin, join, joiningId, load]);

  return (
    <PanelShell
      title={
        <span className="inline-flex items-center gap-2">
          <CardsIcon className="w-5 h-5 opacity-70" />
          Tables Lobby
        </span>
      }
      right={
        <div className="flex items-center gap-2">
          <Button onClick={load} left={<ArrowRightIcon className="w-4 h-4" />}>
            Refresh
          </Button>
        </div>
      }
    >
      {content}
    </PanelShell>
  );
}

// ---------- GamePanel ----------

export function GamePanel(props: any) {
  // Placeholder content so App.tsx can render something for an active table.
  // Accept any props (tableId, onLeave, etc.) to avoid TS incompatibilities.
  const tableId = props?.tableId as string | undefined;

  return (
    <PanelShell
      title={
        <span className="inline-flex items-center gap-2">
          <PlayIcon className="w-5 h-5 opacity-70" />
          {tableId ? `Game — ${tableId}` : "Game"}
        </span>
      }
      right={
        props?.onLeave ? (
          <Button onClick={() => props.onLeave?.()} left={<ArrowRightIcon className="w-4 h-4" />}>
            Leave
          </Button>
        ) : null
      }
    >
      <div className="text-sm opacity-80">
        Game surface coming here. Your core Telegram-bot engine runs gameplay; this panel is a mini-app
        surface for table HUD, actions, and stats. Hook this to your real-time channel when ready.
      </div>
    </PanelShell>
  );
}
