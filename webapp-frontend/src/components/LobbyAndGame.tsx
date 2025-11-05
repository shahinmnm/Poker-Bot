// webapp-frontend/src/components/LobbyAndGame.tsx
import React from "react";
import {
  apiHealth,
  apiTables,
  apiUserSettings,
  apiUserStats,
  apiJoinTable,
  type TableDto,
} from "../lib/api";

type LoadState<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; code?: number; detail?: string };

export default function LobbyAndGame() {
  const [health, setHealth] = React.useState<LoadState<{ status: string; time: string }>>({
    status: "idle",
  });
  const [tables, setTables] = React.useState<LoadState<TableDto[]>>({ status: "idle" });
  const [settings, setSettings] = React.useState<LoadState<any>>({ status: "idle" });
  const [stats, setStats] = React.useState<LoadState<any>>({ status: "idle" });
  const [joining, setJoining] = React.useState<string | null>(null);

  const isTelegram = React.useMemo(() => {
    try {
      // @ts-ignore Telegram WebApp global
      return Boolean((window as any)?.Telegram?.WebApp?.initData?.length);
    } catch {
      return false;
    }
  }, []);

  React.useEffect(() => {
    let cancelled = false;

    async function loadAll() {
      setHealth({ status: "loading" });
      setTables({ status: "loading" });
      setSettings({ status: "loading" });
      setStats({ status: "loading" });

      // Load in parallel, but handle independently
      const jobs = [
        (async () => {
          try {
            const h = await apiHealth();
            if (!cancelled) setHealth({ status: "ready", data: h });
          } catch (e: any) {
            if (!cancelled)
              setHealth({ status: "error", code: e?.code, detail: e?.detail || String(e) });
          }
        })(),
        (async () => {
          try {
            const t = await apiTables();
            if (!cancelled) setTables({ status: "ready", data: t });
          } catch (e: any) {
            if (!cancelled)
              setTables({ status: "error", code: e?.code, detail: e?.detail || String(e) });
          }
        })(),
        (async () => {
          try {
            const s = await apiUserSettings();
            if (!cancelled) setSettings({ status: "ready", data: s });
          } catch (e: any) {
            if (e?.message === "AUTH_REQUIRED") {
              if (!cancelled) setSettings({ status: "error", code: 401, detail: "AUTH_REQUIRED" });
            } else if (!cancelled) {
              setSettings({ status: "error", code: e?.code, detail: e?.detail || String(e) });
            }
          }
        })(),
        (async () => {
          try {
            const s = await apiUserStats();
            if (!cancelled) setStats({ status: "ready", data: s });
          } catch (e: any) {
            if (e?.message === "AUTH_REQUIRED") {
              if (!cancelled) setStats({ status: "error", code: 401, detail: "AUTH_REQUIRED" });
            } else if (!cancelled) {
              setStats({ status: "error", code: e?.code, detail: e?.detail || String(e) });
            }
          }
        })(),
      ];

      await Promise.all(jobs);
    }

    loadAll();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onJoin(tableId: string) {
    try {
      setJoining(tableId);
      await apiJoinTable(tableId);
      // Reload tables after a (simulated) join
      const t = await apiTables();
      setTables({ status: "ready", data: t });
    } catch (e: any) {
      alert(
        e?.code === 401
          ? "You need to open this Mini App inside Telegram to join tables."
          : `Join failed: ${e?.detail || e?.message || e}`
      );
    } finally {
      setJoining(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl p-4 text-[var(--fg,#e5e7eb)]">
      {/* Status bar */}
      <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-3">
        <Card title="Server">
          {health.status === "ready" ? (
            <div className="text-sm">
              <div className="font-medium">OK</div>
              <div className="opacity-70">{health.data.time}</div>
            </div>
          ) : health.status === "loading" ? (
            <div className="opacity-70 text-sm">Checking…</div>
          ) : health.status === "error" ? (
            <div className="text-sm text-red-400">
              Error {health.code || ""} {health.detail || ""}
            </div>
          ) : null}
        </Card>

        <Card title="Settings">
          {settings.status === "ready" ? (
            <pre className="text-xs opacity-90">{JSON.stringify(settings.data, null, 2)}</pre>
          ) : settings.status === "loading" ? (
            <div className="opacity-70 text-sm">Loading…</div>
          ) : settings.status === "error" && settings.code === 401 ? (
            <div className="text-yellow-400 text-sm">
              Not authenticated. Outside Telegram we auto-use <code>user_id=1</code>.
              If you still see this, ensure <code>VITE_API_URL</code> is correct and your Nginx
              <code>/api</code> rewrite is active.
            </div>
          ) : settings.status === "error" ? (
            <div className="text-sm text-red-400">
              Error {settings.code || ""} {settings.detail || ""}
            </div>
          ) : null}
        </Card>

        <Card title="Stats">
          {stats.status === "ready" ? (
            <pre className="text-xs opacity-90">{JSON.stringify(stats.data, null, 2)}</pre>
          ) : stats.status === "loading" ? (
            <div className="opacity-70 text-sm">Loading…</div>
          ) : stats.status === "error" && stats.code === 401 ? (
            <div className="text-yellow-400 text-sm">Stats need identity (Telegram or dev fallback).</div>
          ) : stats.status === "error" ? (
            <div className="text-sm text-red-400">
              Error {stats.code || ""} {stats.detail || ""}
            </div>
          ) : null}
        </Card>
      </div>

      {/* Lobby */}
      <SectionTitle>Lobby</SectionTitle>
      <div className="grid gap-3 md:grid-cols-2">
        {tables.status === "ready" ? (
          tables.data.length ? (
            tables.data.map((t) => (
              <div
                key={t.id}
                className="rounded-2xl p-4 shadow border border-[var(--card-border,#2a2a2a)] bg-[var(--card-bg,#111318)]"
              >
                <div className="flex items-center justify-between">
                  <div className="font-semibold">{t.name}</div>
                  <span className="text-xs px-2 py-0.5 rounded-full border opacity-80">
                    {t.status === "running" ? "Running" : "Waiting"}
                  </span>
                </div>
                <div className="mt-2 text-sm opacity-80">
                  Stakes: {t.stakes} • Players: {t.players_count}/{t.max_players}{" "}
                  {t.is_private ? "• Private" : ""}
                </div>
                <div className="mt-3">
                  <button
                    className="px-3 py-1.5 rounded-lg border shadow-sm text-sm disabled:opacity-50"
                    disabled={joining === t.id}
                    onClick={() => onJoin(t.id)}
                  >
                    {joining === t.id ? "Joining…" : "Join"}
                  </button>
                </div>
              </div>
            ))
          ) : (
            <div className="opacity-70">No tables available.</div>
          )
        ) : tables.status === "loading" ? (
          <div className="opacity-70">Loading tables…</div>
        ) : (
          <div className="text-red-400">
            Failed to load tables {tables.code ? `(HTTP ${tables.code})` : ""}. {tables.detail || ""}
          </div>
        )}
      </div>

      {!isTelegram && (
        <div className="mt-6 text-xs opacity-60">
          Tip: open this Mini App inside Telegram to use your real identity. In dev, we use{" "}
          <code>user_id=1</code> automatically.
        </div>
      )}
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl p-4 shadow border border-[var(--card-border,#2a2a2a)] bg-[var(--card-bg,#111318)]">
      <div className="text-xs uppercase tracking-wide opacity-60">{title}</div>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="mt-6 mb-3 text-lg font-semibold">{children}</h2>;
}
