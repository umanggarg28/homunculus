// Shape of records flowing from FastAPI to the browser.
// Kept narrow and explicit — every field that's optional is marked.

export type ServiceName = "repl" | "heartbeat" | "telegram" | "web" | "feed";
export type MemoryType = "user" | "feedback" | "project" | "reference";

export interface MemoryEntry {
  filename: string;
  name: string;
  description: string;
  type: MemoryType | string;
  mtime: number;
}

export interface LogFile {
  rel: string;
  date: string;
  size_kb: number;
  mtime: number;
}

export interface StatusEntry {
  state: "live" | "idle" | "stale" | "unknown";
  last_seen: number | null;
  age_s: number | null;
}

export type StatusMap = Record<string, StatusEntry>;

export interface WebConfig {
  auth_required: boolean;
}

export interface PersistedChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export interface Chapter {
  id: string;
  title: string;
  opened_at: string | null;
  closed_at: string | null;
  turns: number;
}

export type TaskStatus = "active" | "completed" | "cancelled";
export type Recurrence = "none" | "daily" | "weekly";

export interface TaskRun {
  ts: string;
  status: "success" | "failure";
  result: string;
  duration_s?: number;
}

export interface Task {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  due_at: string | null;
  recurrence: Recurrence;
  notify: boolean;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  last_result: string;
  last_runs: TaskRun[];
}

export interface TasksMeta {
  recurrence_options: Recurrence[];
}

export interface Skill {
  name: string;
  description: string;
  call_count: number;
  success_count: number;
  failure_count: number;
  last_used: string | null;
  last_status: "success" | "failure" | null;
  recent_calls?: string[]; // ISO timestamps in last 24h
}

/** Event kinds emitted into _events.jsonl by every Homunculus service. */
export type EventKind =
  | "user_message"
  | "assistant_reply"
  | "tool_call"
  | "tool_result"
  | "llm_call";

export interface FeedEvent {
  ts: string;
  service: ServiceName | string;
  event: EventKind | string;
  // Event-specific fields — all optional, depend on `event`.
  text?: string;
  name?: string;
  args?: string;
  result?: string;
  model?: string;
  host?: string;
}
