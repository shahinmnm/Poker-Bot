// webapp-frontend/src/lib/api.ts
//
// Frontend API client for Poker WebApp.
// - Uses Telegram WebApp init data when available
// - Outside Telegram (browser/dev), auto-adds ?user_id=1 to avoid 401
// - Normalizes tables payload and exposes typed helpers
// - Distinguishes AUTH (401) from true missing endpoints (404)
// - Exports small utilities used by App (theme + init data)

export type Json = Record<string, any>;

export type TableDto = {
  id: string;
  name: string;
  stakes: string;           // "50/100"
  players_count: number;    // 5
  max_players: number;      // 9
  is_private: boolean;      // true/false
  status: "waiting" | "running";
};

export type UserSettings = {
  user_id: number;
  theme: "auto" | "light" | "dark";
  notifications: boolean;
  locale: string;
  currency: "chips" | "bb";
  experimental: boolean;
};

export type UserStats = {
  user_id: number;
  hands_played: number;
  biggest_win: number;
  biggest_loss: number;
  win_rate: number; // 0..1
  last_played: string; // ISO
  streak_days: number;
  chip_balance: number;
  rank: string;
};

const API_BASE =
  (typeof import.meta !== "undefined" && (import.meta as any).env?.VITE_API_URL) ||
  (typeof window !== "undefined" ? `${window.location.origin}/api` : "/api");

export function getTelegramInitData(): string | null {
  try {
    // @ts-ignore Telegram injected object
    const tg = (window as any)?.Telegram?.WebApp;
    if (tg && typeof tg.initData === "string" && tg.initData.length > 0) return tg.initData;
  } catch {}
  return null;
}

function buildUrl(path: string, query?: Record<string, string | number | boolean>) {
  const base = API_BASE.replace(/\/+$/, "");
  const rel = path.replace(/^\/+/, "");
  const usp = new URLSearchParams();

  if (query) {
    for (const [k, v] of Object.entries(query)) usp.set(k, String(v));
  }

  // Dev fallback outside Telegram
  const hasTg = !!getTelegramInitData();
  if (!hasTg && !usp.has("user_id")) usp.set("user_id", "1");

  return `${base}/${rel}${usp.toString() ? `?${usp.toString()}` : ""}`;
}

async function request<T = Json>(
  method: "GET" | "POST",
  path: string,
  query?: Record<string, string | number | boolean>,
  body?: any
): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
  const initData = getTelegramInitData();
  if (initData) headers.set("X-Telegram-Init-Data", initData);
  if (method === "POST") headers.set("Content-Type", "application/json");

  const res = await fetch(buildUrl(path, query), {
    method,
    headers,
    credentials: "include",
    body: method === "POST" && body != null ? JSON.stringify(body) : undefined,
  });

  // Distinguish common cases for better UI messaging
  if (res.status === 401) {
    const detail = await res.text().catch(() => "");
    const err: any = new Error("AUTH_REQUIRED");
    err.code = 401;
    err.detail = detail;
    throw err;
  }
  if (res.status === 404) {
    const detail = await res.text().catch(() => "");
    const err: any = new Error("NOT_FOUND");
    err.code = 404;
    err.detail = detail;
    throw err;
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    const err: any = new Error(`HTTP_${res.status}`);
    err.code = res.status;
    err.detail = detail;
    throw err;
  }

  const ct = res.headers.get("content-type") || "";
  if (!/application\/json/i.test(ct)) return {} as T;
  return (await res.json()) as T;
}

export function apiHealth() {
  return request<{ status: string; time: string }>("GET", "health");
}

export async function apiTables(): Promise<TableDto[]> {
  const data = await request<{ tables?: TableDto[] } | TableDto[]>("GET", "tables");
  if (Array.isArray(data)) return data;
  return Array.isArray((data as any)?.tables) ? (data as any).tables : [];
}

export function apiUserSettings(query?: { user_id?: number | string }) {
  return request<UserSettings>("GET", "user/settings", query);
}

export function apiUserStats(query?: { user_id?: number | string }) {
  return request<UserStats>("GET", "user/stats", query);
}

export function apiJoinTable(tableId: string, query?: { user_id?: number | string }) {
  return request<Json>("POST", `tables/${encodeURIComponent(tableId)}/join`, query);
}

/* -------- Telegram theme helpers for App -------- */

export function detectTelegramColorScheme(): "light" | "dark" | "auto" {
  try {
    // @ts-ignore
    const tg = (window as any)?.Telegram?.WebApp;
    if (!tg) return "auto";
    const cs: string | undefined = tg.colorScheme;
    if (cs === "light" || cs === "dark") return cs;
  } catch {}
  return "auto";
}

export function watchTelegramTheme(cb: (scheme: "light" | "dark" | "auto") => void) {
  try {
    // @ts-ignore
    const tg = (window as any)?.Telegram?.WebApp;
    if (!tg) return () => {};
    const handler = () => cb(detectTelegramColorScheme());
    tg.onEvent?.("themeChanged", handler);
    return () => tg.offEvent?.("themeChanged", handler);
  } catch {
    return () => {};
  }
}
