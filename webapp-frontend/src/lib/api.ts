// webapp-frontend/src/lib/api.ts
//
// Single, robust API client for the Poker WebApp.
// - Adds X-Telegram-Init-Data when embedded inside Telegram
// - Outside Telegram, appends ?user_id=1 so /user/* won't 401 in dev
// - Normalizes /tables response shape (array or { tables: [...] })
// - Distinguishes 401 (AUTH_REQUIRED) from true 404/other errors
// - Works with both / and /api prefixes transparently

export type Json = Record<string, any>;

const API_BASE =
  (typeof import.meta !== "undefined" && (import.meta as any).env?.VITE_API_URL) ||
  (typeof window !== "undefined" ? `${window.location.origin}/api` : "/api");

function getTelegramInitData(): string | null {
  try {
    // @ts-ignore Telegram WebApp global (present when embedded)
    const tg = (window as any)?.Telegram?.WebApp;
    if (tg && typeof tg.initData === "string" && tg.initData.length > 0) {
      return tg.initData;
    }
  } catch {}
  return null;
}

async function apiGet<T = Json>(
  path: string,
  query?: Record<string, string | number | boolean>
): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });

  const initData = getTelegramInitData();
  if (initData) headers.set("X-Telegram-Init-Data", initData);

  const usp = new URLSearchParams();
  if (query) for (const [k, v] of Object.entries(query)) usp.set(k, String(v));

  // Dev fallback user outside Telegram to avoid 401s
  if (!initData && !usp.has("user_id")) usp.set("user_id", "1");

  const base = API_BASE.replace(/\/+$/, "");
  const rel = path.replace(/^\/+/, "");
  const url = `${base}/${rel}${usp.toString() ? `?${usp.toString()}` : ""}`;

  const res = await fetch(url, { headers, credentials: "include" });

  if (res.status === 401) {
    const detail = await res.text().catch(() => "");
    const err: any = new Error("AUTH_REQUIRED");
    err.code = 401;
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

// ---- Public API used by UI components ----

export async function apiHealth() {
  return apiGet<{ status: string; time: string }>("health");
}

export type TableDto = {
  id: string;
  name: string;
  stakes: string;
  players_count: number;
  max_players: number;
  is_private: boolean;
  status: "waiting" | "running";
};

export async function apiTables(): Promise<TableDto[]> {
  const data = await apiGet<{ tables?: TableDto[] } | TableDto[]>("tables");
  if (Array.isArray(data)) return data;
  return Array.isArray((data as any)?.tables) ? (data as any).tables : [];
}

export async function apiUserSettings() {
  return apiGet("user/settings");
}

export async function apiUserStats() {
  return apiGet("user/stats");
}

export async function apiJoinTable(tableId: string) {
  const initData = getTelegramInitData();
  const usp = new URLSearchParams();
  if (!initData) usp.set("user_id", "1"); // dev fallback

  const base = API_BASE.replace(/\/+$/, "");
  const url = `${base}/tables/${encodeURIComponent(tableId)}/join${
    usp.toString() ? `?${usp.toString()}` : ""
  }`;

  const res = await fetch(url, {
    method: "POST",
    headers: initData ? { "X-Telegram-Init-Data": initData } : undefined,
    credentials: "include",
  });

  if (res.status === 401) {
    const detail = await res.text().catch(() => "");
    const err: any = new Error("AUTH_REQUIRED");
    err.code = 401;
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
  return /application\/json/i.test(ct) ? await res.json() : {};
}
