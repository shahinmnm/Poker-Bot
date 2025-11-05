import React, { useEffect, useMemo, useState } from 'react';

/**
 * LobbyAndGame.tsx
 *
 * Adds:
 *  - LobbyPanel: browse active tables, quick filter, create new table, join
 *  - GamePanel: shows the selected table (from localStorage) with minimal HUD
 *
 * Backend (optional, graceful if missing):
 *  - GET  /api/tables                  -> list tables [{id,name,bb,maxPlayers,seated,private}]
 *  - POST /api/tables                  -> create {name, bb, maxPlayers, private}
 *  - POST /api/tables/:id/join         -> join table
 *  - GET  /api/tables/:id              -> table details
 *
 * If these endpoints are not available yet, panels use a local mock dataset
 * and still let the user “join” (stored in localStorage as activeTableId).
 *
 * Props to pass (mirrors other panels in this project):
 *  - sessionToken: string | null
 *  - userId: number | null
 *  - username: string | null
 */

type Nullable<T> = T | null;

type TableSummary = {
  id: string;
  name: string;
  bb: number;          // big blind (chip unit)
  maxPlayers: number;  // seats
  seated: number;      // occupied seats
  private: boolean;    // invite-only?
};

type TableDetail = TableSummary & {
  // Extend with simple HUD info (for display in GamePanel)
  pot?: number;
  dealer?: string;
  stage?: 'preflop' | 'flop' | 'turn' | 'river' | 'showdown' | 'idle';
  players?: Array<{ id: number; name: string; stack: number; sittingOut?: boolean }>;
};

type CreateTablePayload = {
  name: string;
  bb: number;
  maxPlayers: number;
  private: boolean;
};

type Props = {
  sessionToken: Nullable<string>;
  userId: Nullable<number>;
  username: Nullable<string>;
};

/** ---------- Utilities ---------- */

const fmtNum = (n: number | undefined | null, digits = 0) =>
  typeof n === 'number' && !Number.isNaN(n) ? n.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—';

const chips = (n: number | undefined | null) => (typeof n === 'number' ? `${fmtNum(n)} 🪙` : '—');

async function safeFetch<T>(
  path: string,
  opts: RequestInit,
  token?: string | null,
): Promise<{ ok: boolean; data?: T; status: number }> {
  try {
    const res = await fetch(path, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        ...(opts.headers || {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      credentials: 'include',
    });
    if (!res.ok) return { ok: false, status: res.status };
    const data = (await res.json()) as T;
    return { ok: true, data, status: res.status };
  } catch {
    return { ok: false, status: 0 };
  }
}

const Card: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <div style={{
    padding: 12,
    borderRadius: 12,
    background: 'var(--tg-theme-secondary-bg-color, rgba(255,255,255,0.04))',
    border: '1px solid rgba(255,255,255,0.12)',
    ...style
  }}>
    {children}
  </div>
);

const Row: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, margin: '6px 0' }}>
    <div style={{ opacity: 0.8, fontSize: 13 }}>{label}</div>
    <div style={{ fontWeight: 600, fontSize: 13 }}>{value}</div>
  </div>
);

const Button: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement>> = ({ children, style, ...rest }) => (
  <button
    {...rest}
    style={{
      padding: '10px 12px',
      borderRadius: 12,
      border: '1px solid rgba(255,255,255,0.12)',
      background: 'linear-gradient(180deg, rgba(40,40,40,0.9), rgba(20,20,20,0.9))',
      color: 'var(--tg-theme-text-color, #fff)',
      fontWeight: 800,
      fontSize: 14,
      boxShadow: '0 6px 18px rgba(0,0,0,0.25)',
      ...style,
    }}
  >
    {children}
  </button>
);

const Input: React.FC<React.InputHTMLAttributes<HTMLInputElement>> = ({ style, ...rest }) => (
  <input
    {...rest}
    style={{
      width: '100%',
      padding: '10px 12px',
      borderRadius: 10,
      border: '1px solid rgba(255,255,255,0.2)',
      background: 'transparent',
      color: 'inherit',
      outline: 'none',
      fontSize: 14,
      ...style,
    }}
  />
);

const Select: React.FC<React.SelectHTMLAttributes<HTMLSelectElement>> = ({ style, children, ...rest }) => (
  <select
    {...rest}
    style={{
      width: '100%',
      padding: '10px 12px',
      borderRadius: 10,
      border: '1px solid rgba(255,255,255,0.2)',
      background: 'transparent',
      color: 'inherit',
      outline: 'none',
      fontSize: 14,
      ...style,
    }}
  >
    {children}
  </select>
);

/** ---------- Mock data (used when backend endpoints are missing) ---------- */

const MOCK_TABLES: TableSummary[] = [
  { id: 't1', name: 'Quick Match', bb: 2, maxPlayers: 6, seated: 4, private: false },
  { id: 't2', name: 'Friends Only', bb: 1, maxPlayers: 9, seated: 3, private: true },
  { id: 't3', name: 'Deep Stack', bb: 5, maxPlayers: 6, seated: 5, private: false },
];

function mockDetail(id: string): TableDetail {
  const base = MOCK_TABLES.find(t => t.id === id) || MOCK_TABLES[0];
  return {
    ...base,
    pot: Math.floor(Math.random() * 200) + 40,
    dealer: ['Alice', 'Bob', 'Dana', 'Eve'][Math.floor(Math.random() * 4)],
    stage: ['preflop', 'flop', 'turn', 'river', 'showdown'][Math.floor(Math.random() * 5)] as TableDetail['stage'],
    players: [
      { id: 11, name: 'Alice', stack: 180 },
      { id: 12, name: 'Bob', stack: 220 },
      { id: 13, name: 'You', stack: 200 },
      { id: 14, name: 'Dana', stack: 150 },
    ].slice(0, base.seated),
  };
}

/** ---------- Local storage for game selection ---------- */

const ACTIVE_TABLE_KEY = 'activeTableId';

function saveActiveTable(id: string | null) {
  if (id) localStorage.setItem(ACTIVE_TABLE_KEY, id);
  else localStorage.removeItem(ACTIVE_TABLE_KEY);
}

function loadActiveTable(): string | null {
  return localStorage.getItem(ACTIVE_TABLE_KEY);
}

/** ---------- LobbyPanel ---------- */

export const LobbyPanel: React.FC<Props> = ({ sessionToken }) => {
  const [tables, setTables] = useState<TableSummary[]>([]);
  const [filter, setFilter] = useState('');
  const [serverAvailable, setServerAvailable] = useState(true);
  const [creating, setCreating] = useState(false);

  const [name, setName] = useState('Friends Table');
  const [bb, setBb] = useState(1);
  const [maxPlayers, setMaxPlayers] = useState(6);
  const [isPrivate, setIsPrivate] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    (async () => {
      const res = await safeFetch<TableSummary[]>('/api/tables', { method: 'GET' }, sessionToken);
      if (!mounted) return;
      if (res.ok && Array.isArray(res.data)) {
        setTables(res.data);
        setServerAvailable(true);
      } else {
        // fallback to mock
        setTables(MOCK_TABLES);
        setServerAvailable(false);
      }
    })();
    return () => { mounted = false; };
  }, [sessionToken]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return tables;
    return tables.filter(t =>
      t.name.toLowerCase().includes(q) ||
      String(t.bb).includes(q) ||
      (t.private ? 'private' : 'public').includes(q)
    );
  }, [tables, filter]);

  const join = async (tableId: string) => {
    setMessage(null);
    const res = await safeFetch(`/api/tables/${encodeURIComponent(tableId)}/join`, { method: 'POST' }, sessionToken);
    if (res.ok) {
      saveActiveTable(tableId);
      setMessage('Joined table ✅ Open the Game tab to play.');
    } else {
      // graceful: still allow local join
      saveActiveTable(tableId);
      setMessage('Joined locally (server not ready). Open the Game tab to play.');
    }
  };

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setMessage(null);
    const payload: CreateTablePayload = { name, bb: Number(bb), maxPlayers: Number(maxPlayers), private: isPrivate };
    const res = await safeFetch<TableSummary>('/api/tables', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, sessionToken);

    if (res.ok && res.data) {
      setTables(prev => [res.data!, ...prev]);
      setMessage('Table created ✅ You can join it now.');
    } else {
      // graceful create: synthesize local
      const id = `t${Math.floor(Math.random() * 9000) + 1000}`;
      const newTable: TableSummary = { id, name, bb: Number(bb), maxPlayers: Number(maxPlayers), seated: 1, private: isPrivate };
      setTables(prev => [newTable, ...prev]);
      setMessage('Table created locally (server not ready).');
    }
    setCreating(false);
  };

  return (
    <div style={{ padding: 12, display: 'grid', gap: 12 }}>
      {!serverAvailable && (
        <Card style={{ background: 'rgba(255, 199, 0, 0.12)', border: '1px solid rgba(255, 199, 0, 0.35)' }}>
          Server tables endpoint not found — using local mock data.
          Implement:
          <code style={{ marginLeft: 6 }}>/api/tables</code>
        </Card>
      )}

      <div style={{ display: 'grid', gap: 8 }}>
        <div style={{ fontSize: 18, fontWeight: 800 }}>Find a table</div>
        <Input placeholder="Search by name, stake, visibility…" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>

      <Card>
        <div style={{ display: 'grid', gap: 8 }}>
          {filtered.map(t => (
            <div key={t.id} style={{
              display: 'grid',
              gridTemplateColumns: '1fr auto',
              gap: 8,
              padding: '10px 0',
              borderBottom: '1px dashed rgb
