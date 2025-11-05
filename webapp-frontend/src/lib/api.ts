// Tiny, robust fetch wrapper for the Poker WebApp frontend.
// - Adds X-Telegram-Init-Data when embedded in Telegram
// - Outside Telegram, appends ?user_id=1 so settings/stats don’t 401
// - Normalizes /tables response
// - Distinguishes 401 ("AUTH_REQUIRED") from true 404 ("NOT_FOUND")
// - Works for both GET and POST (e.g. /tables/{id}/join)

export type Json = Record<string, any>;

// Toggle ad-hoc logging by setting: window.__POKER_DEBUG__ = true
const DEBUG: boolean =
  typeof window !== "undefined" && Boolean((window as any).__POKER_DEBUG__);

const API_BASE: string =
  (typeof import.meta !== "undefined" &&
    (import.meta as any).env?.VITE_API_URL) ||
  (typeof window !== "undefined"
    ? `${window.location.origin}/api`
    : "/api");

// --- Telegram helpers --------------------------------------------------------
function getTelegramInitData(): string | null {
  try {
    // @ts-ignore injected by Telegram client only inside real WebApp
    const tg = (window as any)?.Telegram?.WebApp;
    if (tg && typeof tg.initData === "string" && tg.initData.length > 0) {
      return tg.initData;
    }
  } catch {}
  return null;
}

function inTelegram(): boolean {
  return getTelegramInitData() !== null;
}

// --- URL builder -------------------------------------------------------------
function buildUrl(path: string, query?: Record<string, string | number | boolean>) {
  const base = (API_BASE || "/api").replace(/\/+$/, "");
  const rel = String(path || "").replace(/^\/+/, "");
  const usp = new URLSearchParams();

  if (query) {
    for (const [k, v] of Object.entries(query)) usp.set(k, String(v));
  }

  // Outside Telegram, force a dev identity so the app doesn't 401 in browsers
  if (!inTelegram() && !usp.has("user_id")) usp.set("user_id", "1");

  const url = `${base}/${rel}${usp.toString() ? `?${usp.toString()}` : ""}`;

  if (DEBUG) {
    // eslint-disable-next-line no-console
    console.debug("[API] →", { url, hasInitData: inTelegram() });
  }
  return url;
}

// --- Core request ------------------------------------------------------------
async function apiRequest<T = Json>(
  method: "GET" | "POST",
  path: string,
  opts?: {
    query?: Record<string, string | number | boolean>;
    body?: Json | FormData | undefined;
  }
): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
  const initData = getTelegramInitData();
  if (initData) headers.set("X-Telegram-Init-Data", initData);

  const isForm = typeof FormData !== "undefined" && opts?.body instanceof FormData;
  if (!isForm && opts?.body && method === "POST") {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(buildUrl(path, opts?.query), {
    method,
    headers,
    body: method === "POST" ? (isForm ? (opts?.body as any) : JSON.stringify(opts?.body ?? {})) : undefined,
    credentials: "include",
  });

  // 401 is *not* "missing" — just unauthenticated
  if (res.status === 401) {
    const detail = await res.text().catch(() => "");
    const err: any = new Error("AUTH_REQUIRED");
    err.code = 401;
    err.detail = detail;
    throw err;
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    const err: any = new Error(res.status === 404 ? "NOT_FOUND" : `HTTP_${res.status}`);
    err.code = res.status;
    err.detail = detail;
    throw err;
  }

  const ct = res.headers.get("content-type") || "";
  if (!/application\/json/i.test(ct)) return {} as T;
  return (await res.json()) as T;
}

async function apiGet<T = Json>(
  path: string,
  query?: Record<string, string | number | boolean>
): Promise<T> {
  return apiRequest<T>("GET", path, { query });
}

async function apiPost<T = Json>(
  path: string,
  body?: Json | FormData,
  query?: Record<string, string | number | boolean>
): Promise<T> {
  return apiRequest<T>("POST", path, { body, query });
}

// ---- Public API used by the UI ---------------------------------------------

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
  // Backend may return either an array or { tables: [...] }
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
  // POST with auth semantics identical to GETs
  return apiPost(`tables/${encodeURIComponent(tableId)}/join`);
}
