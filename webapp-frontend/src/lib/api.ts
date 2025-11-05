// webapp-frontend/src/lib/api.ts
//
// Tiny, robust fetch wrapper for the Poker WebApp frontend.
// - Adds X-Telegram-Init-Data when embedded in Telegram
// - Outside Telegram, appends ?user_id=1 so dev flows don't 401
// - Inside Telegram, if backend still replies 401, RETRY once with ?user_id=1 (demo fallback)
// - Normalizes tables response shape
// - Distinguishes 401 ("AUTH_REQUIRED") from real missing endpoints (404)

export type Json = Record<string, any>;

const API_BASE =
  (typeof import.meta !== "undefined" && (import.meta as any).env?.VITE_API_URL) ||
  (typeof window !== "undefined" ? `${window.location.origin}/api` : "/api");

function getTelegramInitData(): string | null {
  try {
    // @ts-ignore - Telegram WebApp injected object
    const tg = (window as any)?.Telegram?.WebApp;
    if (tg && typeof tg.initData === "string" && tg.initData.length > 0) {
      return tg.initData;
    }
  } catch {}
  return null;
}

async function doFetch(url: string, headers: Headers) {
  const res = await fetch(url, { headers, credentials: "include" });
  return res;
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

  // Outside Telegram, force a dev user so stats/settings won't 401
  if (!initData && !usp.has("user_id")) usp.set("user_id", "1");

  const base = API_BASE.replace(/\/+$/, "");
  const rel = path.replace(/^\/+/, "");
  const url = `${base}/${rel}${usp.toString() ? `?${usp.toString()}` : ""}`;

  // First attempt
  let res = await doFetch(url, headers);

  // Inside Telegram some backends may not verify init-data yet → try once with user_id=1
  if (res.status === 401 && initData && !usp.has("user_id")) {
    const usp2 = new URLSearchParams(usp);
    usp2.set("user_id", "1");
    const url2 = `${base}/${rel}?${usp2.toString()}`;
    res = await doFetch(url2, headers);
    if ((res as any).__retried !== true) (res as any).__retried = true;
  }

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

// ---- Public API used by the UI ----

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
  if (!initData) usp.set("user_id", "1");

  const base = API_BASE.replace(/\/+$/, "");
  const url = `${base}/tables/${encodeURIComponent(tableId)}/join${
    usp.toString() ? `?${usp.toString()}` : ""
  }`;

  const headers: HeadersInit = {};
  if (initData) (headers as any)["X-Telegram-Init-Data"] = initData;

  let res = await fetch(url, { method: "POST", headers, credentials: "include" });

  // Telegram but backend still 401 → demo fallback
  if (res.status === 401 && initData && !usp.has("user_id")) {
    const url2 = `${base}/tables/${encodeURIComponent(tableId)}/join?user_id=1`;
    res = await fetch(url2, { method: "POST", headers, credentials: "include" });
  }

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
