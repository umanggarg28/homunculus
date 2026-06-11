// Typed fetch wrappers. Every network call goes through here so route
// changes are one-line edits and tests can mock a single module.

/**
 * Parse a naive ISO datetime string from the server as UTC.
 * The server (Docker) runs in UTC and stores datetimes without timezone info.
 * JavaScript's `new Date("2026-06-01T03:30:00")` treats no-timezone strings
 * as LOCAL time — wrong. Appending "Z" forces UTC interpretation.
 */
export function parseServerIso(iso: string): number {
  if (!iso) return NaN;
  const hasZone = iso.endsWith("Z") || iso.includes("+") || /[+-]\d{2}:\d{2}$/.test(iso);
  return new Date(hasZone ? iso : iso + "Z").getTime();
}

/**
 * Parse a TASK timestamp (due_at, last_runs[].ts, last_fired_at).
 *
 * The task store writes these as NAIVE wall-clock strings in the USER's
 * timezone (tasks.py now_user_naive) — and the user's timezone is
 * autodetected from this very browser, so the browser's local zone IS
 * the correct frame. JS already parses zoneless ISO strings as local
 * time, which is exactly what we want here.
 *
 * Do NOT use parseServerIso for task fields: appending "Z" reads them
 * as UTC and shifts every countdown by the UTC offset (observed live:
 * "due in 7h" for a task due in 1.5h, IST = +5:30).
 */
export function parseTaskWallClock(iso: string): number {
  if (!iso) return NaN;
  return new Date(iso).getTime();
}

import type {
  AgentControls,
  AgentBudgetStats,
  AgentReplayTurn,
  Chapter,
  MemoryEntry,
  LogFile,
  PersistedChatMessage,
  Skill,
  StatusMap,
  Task,
  TasksMeta,
  TaskStatus,
  WebConfig,
} from "./types";

export const API_BASE = "/api";
const TOKEN_STORAGE_KEY = "homunculus.webToken";

export function getWebToken(): string {
  return localStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
}

export function setWebToken(token: string): void {
  const trimmed = token.trim();
  if (trimmed) localStorage.setItem(TOKEN_STORAGE_KEY, trimmed);
  else localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export function eventStreamUrl(): string {
  const token = getWebToken();
  return token ? `/events?token=${encodeURIComponent(token)}` : "/events";
}

export function authHeaders(extra: HeadersInit = {}): HeadersInit {
  const token = getWebToken();
  return token ? { ...extra, "X-Homunculus-Token": token } : extra;
}

async function jsonGet<T>(path: string): Promise<T> {
  const r = await fetch(API_BASE + path, {
    headers: authHeaders({ Accept: "application/json" }),
  });
  if (!r.ok) throw new Error(`GET ${path}: ${r.status} ${r.statusText}`);
  return (await r.json()) as T;
}

export const api = {
  config: () => jsonGet<WebConfig>("/config"),
  status: () => jsonGet<StatusMap>("/status"),
  chatHistory: () => jsonGet<PersistedChatMessage[]>("/chat/history"),
  memoryList: () => jsonGet<MemoryEntry[]>("/memory"),
  memoryEntry: (filename: string) =>
    fetch(`${API_BASE}/memory/${encodeURIComponent(filename)}/raw`, {
      headers: authHeaders(),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Memory not found: ${filename}`);
      return r.text();
    }),
  logsList: () => jsonGet<LogFile[]>("/logs"),
  logEntry: (rel: string) =>
    fetch(`${API_BASE}/logs/${rel}/raw`, {
      headers: authHeaders(),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Log not found: ${rel}`);
      return r.text();
    }),

  /** POST a chat message; returns the raw streaming Response so the
   * caller can read the SSE body without buffering. The optional
   * stream_id lets the caller cancel the stream via chatCancel(). */
  chatSend: (message: string, stream_id?: string) =>
    fetch(`${API_BASE}/chat/send`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ message, stream_id }),
    }),

  chatCancel: (stream_id: string) =>
    fetch(`${API_BASE}/chat/cancel`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ stream_id }),
    }).then(async (r) => r.json() as Promise<{ ok: boolean; reason?: string }>),

  memoryUpdate: (filename: string, body: string) =>
    fetch(`${API_BASE}/memory/${encodeURIComponent(filename)}/raw`, {
      method: "PUT",
      credentials: "include",
      headers: authHeaders({ "Content-Type": "text/plain" }),
      body,
    }).then((r) => {
      if (!r.ok) throw new Error(`Update memory failed: ${r.status}`);
      return r.json() as Promise<{ ok: boolean; bytes: number }>;
    }),

  memoryDelete: (filename: string) =>
    fetch(`${API_BASE}/memory/${encodeURIComponent(filename)}`, {
      method: "DELETE",
      headers: authHeaders(),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Delete failed: ${filename}`);
      return r.json() as Promise<{ ok: boolean; message: string }>;
    }),

  chaptersList: () => jsonGet<Chapter[]>("/chapters"),

  skillsList: () => jsonGet<Skill[]>("/skills"),

  agentControls: () => jsonGet<AgentControls>("/agent/controls"),

  agentControlsUpdate: (body: Partial<AgentControls>) =>
    fetch(`${API_BASE}/agent/controls`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Update controls failed: ${r.status}`);
      return r.json() as Promise<AgentControls>;
    }),

  agentReplay: (limit: number = 12) =>
    jsonGet<AgentReplayTurn[]>(`/agent/replay?limit=${limit}`),

  modeGet: () => jsonGet<{ mode: "plan" | "build" }>("/mode"),

  modeSet: (mode: "plan" | "build") =>
    fetch(`${API_BASE}/mode`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ mode }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Set mode failed: ${r.status}`);
      return r.json() as Promise<{ mode: "plan" | "build" }>;
    }),

  tasksList: (status: TaskStatus | "all" = "all") =>
    jsonGet<Task[]>(`/tasks?status=${status}`),

  tasksMeta: () => jsonGet<TasksMeta>("/tasks/meta"),

  agentUpcoming: () => jsonGet<{
    next_tick: string | null;
    default_interval_min: number;
    next_task: { id: string; title: string; due_at: string; recurrence: string } | null;
  }>("/agent/upcoming"),

  statsToday: () => jsonGet<AgentBudgetStats>("/stats/today"),

  tasksCreate: (body: {
    title: string;
    description?: string;
    due_at?: string | null;
    recurrence?: string;
    notify?: boolean;
  }) =>
    fetch(`${API_BASE}/tasks`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Create task failed: ${r.status}`);
      return r.json() as Promise<Task>;
    }),

  tasksUpdate: (id: string, body: Partial<{
    title: string;
    description: string;
    due_at: string | null;
    recurrence: string;
    notify: boolean;
  }>) =>
    fetch(`${API_BASE}/tasks/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Update task failed: ${r.status}`);
      return r.json() as Promise<Task>;
    }),

  tasksGet: (id: string) =>
    jsonGet<Task>(`/tasks/${encodeURIComponent(id)}`),

  tasksRunNow: (id: string) =>
    fetch(`${API_BASE}/tasks/${encodeURIComponent(id)}/run-now`, {
      method: "POST",
      headers: authHeaders(),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Run-now failed: ${r.status}`);
      return r.json() as Promise<Task>;
    }),

  tasksCancel: (id: string, reason: string = "") =>
    fetch(`${API_BASE}/tasks/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ reason }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Cancel failed: ${r.status}`);
      return r.json() as Promise<Task>;
    }),

  tasksDelete: (id: string) =>
    fetch(`${API_BASE}/tasks/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: authHeaders(),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Delete task failed: ${r.status}`);
      return r.json();
    }),

  contextGauge: () =>
    jsonGet<{ used_tokens: number; limit_tokens: number; model: string; pct: number }>("/context"),

  chapterClose: () =>
    fetch(`${API_BASE}/chapters/close`, {
      method: "POST",
      headers: authHeaders(),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Close chapter failed`);
      return r.json() as Promise<{ ok: boolean; id: string; title: string }>;
    }),
};
