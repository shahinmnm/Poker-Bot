// webapp-frontend/src/lib/api.ts
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


// Example join call — used by your "Join" button handler
export async function apiJoinTable(tableId: string) {
const initData = getTelegramInitData();
const usp = new URLSearchParams();
if (!initData) usp.set("user_id", "1");


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
