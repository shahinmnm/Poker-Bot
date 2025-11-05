import React, { useEffect, useMemo, useState } from "react";
import { ChipIcon, CrownIcon, LockIcon, PlayIcon, UsersIcon } from "./icons";

// -------------------------------------------------------------
// Lobby + Table preview + Join flow for Poker WebApp mini-app.
// This component fetches:
//   - /api/tables
// and POSTs:
//   - /api/tables/{table_id}/join
//
// IMPORTANT: Outside Telegram we auto-attach ?user_id=1 so the
// backend doesn't 401 during local/dev testing.
// -------------------------------------------------------------

type TableDto = {
  id: string;
  name: string;
  stakes: string;
  players_count: number;
  max_players: number;
  is_private: boolean;
  status: "waiting" | "running";
};

type FetchResult<T> = { ok: boolean; data?: T; status: number };

// Detect Telegram WebApp context
function getTelegramInitData(): string | null {
  try {
    // @ts-ignore injected by Telegram
    const tg = (window as any)?.Telegram?.WebApp;
    if (tg && typeof tg.initData === "string" && tg.initData.length > 0) {
      return tg.initData;
    }
  } catch {}
  return null;
}

// Tiny fetch helper with dev identity fallback.
// - If outside Telegram, append ?user_id=1 (if not present)
// - Always include credentials and JSON handling
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

export default function LobbyAndGame() {
  const [tables, setTables] = useState<TableDto[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [serverTables, setServerTables] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [joining, setJoining] = useState<string | null>(null);
  const [joinMsg, setJoinMsg] = useState<string | null>(null);

  const initData = useMemo(getTelegramInitData, []);

  // Fetch tables
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      setJoinMsg(null);

      const res = await safeFetch<{ tables?: TableDto[] } | TableDto[]>(
        "/api/tables",
        { method: "GET" },
        initData
      );

      if (cancelled) return;

      if (res.ok) {
        const payload = res.data ?? {};
        const list = Array.isArray(payload)
          ? (payload as TableDto[])
          : Array.isArray((payload as any).tables)
          ? ((payload as any).tables as TableDto[])
          : [];
        setTables(list);
        setServerTables(true);
      } else {
        // Only mark endpoint missing if it's truly 404/Not Found.
        // Other statuses (401, 500, etc.) shouldn't claim "missing".
        if (res.status === 404) {
          setServerTables(false);
          // show a small hint but don't block UI
          setError(
            "Server tables endpoint not found — using local mock data. Implement: /api/tables"
          );
          setTables([
            {
              id: "pub-1",
              name: "Main Lobby",
              stakes: "50/100",
              players_count: 5,
              max_players: 9,
              is_private: false,
              status: "waiting",
            },
            {
              id: "grp-777",
              name: "Friends Table",
              stakes: "10/20",
              players_count: 3,
              max_players: 6,
              is_private: true,
              status: "waiting",
            },
          ]);
        } else {
          // Generic error (incl. 401 in dev, server hiccups, etc.)
          setError(
            `Failed to load tables (HTTP ${res.status || "ERR"}). Retrying or refreshing may help.`
          );
          setTables([]);
          setServerTables(null);
        }
      }

      setLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [initData]);

  async function onJoin(tableId: string) {
    setJoining(tableId);
    setJoinMsg(null);
    try {
      const res = await safeFetch<{ ok?: boolean; message?: string }>(
        `/api/tables/${encodeURIComponent(tableId)}/join`,
        { method: "POST" },
        initData
      );
      if (res.ok) {
        setJoinMsg(`You joined "${tableId}" successfully.`);
      } else {
        if (res.status === 404) {
          setJoinMsg(
            `Join route /api/tables/${tableId}/join is missing (404). Please expose it in the API.`
          );
        } else if (res.status === 401) {
          setJoinMsg(
            "Unauthorized (401). In Telegram the init data will be used automatically; in dev we attach user_id=1."
          );
        } else {
          setJoinMsg(`Failed to join (HTTP ${res.status}).`);
        }
      }
    } catch (e: any) {
      setJoinMsg(e?.message || "Join failed.");
    } finally {
      setJoining(null);
      // small refresh of table list
      const res = await safeFetch<{ tables?: TableDto[] } | TableDto[]>(
        "/api/tables",
        { method: "GET" },
        initData
      );
      if (res.ok) {
        const payload = res.data ?? {};
        const list = Array.isArray(payload)
          ? (payload as TableDto[])
          : Array.isArray((payload as any).tables)
          ? ((payload as any).tables as TableDto[])
          : [];
        setTables(list);
      }
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <header className="flex items-center justify-between">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <CrownIcon className="w-5 h-5" /> Tables
        </h2>
        {serverTables === false && (
          <span className="text-xs text-amber-500">
            Using local mock data (add /api/tables to backend)
          </span>
        )}
      </header>

      {error && (
        <div className="rounded-lg p-3 bg-red-500/10 border border-red-500/30 text-red-200 text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-sm opacity-60">Loading tables…</div>
      ) : tables && tables.length > 0 ? (
        <div className="grid gap-2">
          {tables.map((t) => (
            <div
              key={t.id}
              className="rounded-xl border border-white/10 bg-white/5 p-3 md:p-4 flex items-center justify-between"
            >
              <div className="flex items-center gap-3">
                {t.is_private ? (
                  <LockIcon className="w-5 h-5 opacity-70" />
                ) : (
                  <UsersIcon className="w-5 h-5 opacity-70" />
                )}
                <div>
                  <div className="font-medium">{t.name}</div>
                  <div className="text-xs opacity-70">
                    Stakes {t.stakes} · {t.players_count}/{t.max_players} ·{" "}
                    {t.status === "running" ? "Running" : "Waiting"}
                  </div>
                </div>
              </div>

              <button
                disabled={joining === t.id}
                onClick={() => onJoin(t.id)}
                className="inline-flex items-center gap-2 rounded-lg px-3 py-2 border border-white/20 hover:border-white/40"
              >
                <PlayIcon className="w-4 h-4" />
                {joining === t.id ? "Joining…" : "Join"}
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-sm opacity-60">No tables.</div>
      )}

      {joinMsg && (
        <div className="rounded-lg p-3 bg-emerald-500/10 border border-emerald-500/30 text-emerald-200 text-sm">
          {joinMsg}
        </div>
      )}

      <footer className="flex items-center gap-2 text-xs opacity-70">
        <ChipIcon className="w-4 h-4" />
        <span>Dark/Light follows device. Account settings saved on server.</span>
      </footer>
    </div>
  );
}
