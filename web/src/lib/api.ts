import type { StepOutcome } from "@/lib/toolSteps";

/** The server's HTTP API, as the console uses it. */

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
  /** Written by the program on the operator's behalf, such as an approval outcome. */
  notice?: boolean;
}

export interface Approval {
  id: string;
  tool: string;
  /** The exact arguments the agent proposed, as JSON text. */
  arguments: string;
  /** "ask": approve or decline. "hand off": the operator does it themselves. */
  kind: "ask" | "hand off";
  status: "pending" | "approved" | "denied" | "done";
  created: string;
  decided: string | null;
  note: string | null;
  result: string | null;
  /** Whether the agent has been told the outcome: "replied", or "failed: <why>". */
  follow_up: string | null;
}

export interface ActivityStep {
  time: string;
  name: string;
  arguments: string;
  answer: string;
  result: string;
  outcome: StepOutcome;
  /** The approval request this step created, or ran once it was approved. */
  request: string | null;
}

/** The operator's verdict on a piece of work. */
export type Rating = "up" | "down";

/** One piece of work: a message and everything the agent did for it. */
export interface Job {
  job: string;
  started: string;
  ended: string | null;
  /** The id of the job's root span, which a rating is attached to. */
  span: string | null;
  /** Null only if the turn's start was never recorded. */
  message: string | null;
  /** The turn that told the agent the outcome of a decision, not a typed message. */
  notice: boolean;
  steps: ActivityStep[];
  reply: string | null;
  error: string | null;
  feedback: { rating: Rating; reason: string | null; time: string } | null;
  /** What the job's model calls cost, in US dollars, or null if none reported a cost.
   *  Missing from a server that predates costs. */
  cost?: number | null;
  /** The limit that ended the job, if one did: "steps", "minutes", "cost" or "budget". */
  limit?: string | null;
}

/** This month's spend on the API key against the budget, in US dollars. */
export interface MonthBudget {
  spent: number;
  budget: number;
  unprompted_stop_at: number;
}

async function sendDecision(id: string, action: "approve" | "deny" | "done", note?: string): Promise<Approval> {
  const response = await fetch(`/api/approvals/${id}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note: note || null }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `request failed (${response.status})`);
  }
  return response.json();
}

export const api = {
  /** Start a turn and watch its events. Closing this stream does not stop the turn. */
  chat(message: string, signal?: AbortSignal): Promise<Response> {
    return fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
      signal,
    });
  },

  /** The turn in progress, if any. */
  async currentTurn(): Promise<{ id: string } | null> {
    const response = await fetch("/api/turns/current");
    if (response.status === 204) return null;
    if (!response.ok) throw new Error(`current turn failed: ${response.status}`);
    return response.json();
  },

  /** A turn's events after `lastEventId`, then live: the server-sent events way to
   *  rejoin or reconnect. */
  turnEvents(id: string, lastEventId: number, signal?: AbortSignal): Promise<Response> {
    // Sent only once an event has been seen, as a browser's EventSource does.
    const headers: Record<string, string> = lastEventId ? { "Last-Event-ID": String(lastEventId) } : {};
    return fetch(`/api/turns/${id}/events`, { headers, signal });
  },

  /** Ask the server to stop a running turn. */
  async stopTurn(id: string): Promise<void> {
    const response = await fetch(`/api/turns/${id}/stop`, { method: "POST" });
    if (!response.ok && response.status !== 409) throw new Error(`stop failed: ${response.status}`);
  },

  /** Pending requests first, then the most recently decided ones. */
  async approvals(): Promise<Approval[]> {
    const response = await fetch("/api/approvals");
    if (!response.ok) throw new Error(`approvals failed: ${response.status}`);
    return response.json();
  },

  /** Run exactly the requested action; the agent is told the result. */
  approve: (id: string) => sendDecision(id, "approve"),
  /** Decline; nothing runs and the agent is told, with the reason if given. */
  deny: (id: string, reason?: string) => sendDecision(id, "deny", reason),
  /** Mark a hand-off as done by the operator; the agent is told. */
  done: (id: string, note?: string) => sendDecision(id, "done", note),

  /** This month's spend against the budget, or null when the server keeps no budget. */
  async budget(): Promise<MonthBudget | null> {
    const response = await fetch("/api/budget");
    if (response.status === 204) return null;
    if (!response.ok) throw new Error(`budget failed: ${response.status}`);
    return response.json();
  },

  /** What the agent did, one job per turn, newest first. */
  async activity(): Promise<Job[]> {
    const response = await fetch("/api/activity");
    if (!response.ok) throw new Error(`activity failed: ${response.status}`);
    return response.json();
  },

  /** Rate one piece of work. A later rating replaces an earlier one. */
  async feedback(job: string, rating: Rating, reason?: string): Promise<Job> {
    const response = await fetch(`/api/activity/${job}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating, reason: reason || null }),
    });
    if (!response.ok) throw new Error(`feedback failed: ${response.status}`);
    return response.json();
  },

  /** The conversation so far: the operator's messages and the replies. */
  async history(): Promise<HistoryMessage[]> {
    const response = await fetch("/api/history");
    if (!response.ok) throw new Error(`history failed: ${response.status}`);
    return response.json();
  },
};

/** A wiki page as the page list shows it. */
export interface MemoryPageInfo {
  path: string;
  title: string;
  summary: string;
  guesses: number;
}

/** A page the operator can read and edit: its markdown, and the version it was read at. */
export interface MemoryPage {
  path: string;
  text: string;
  version: string;
}

export interface MemorySource {
  path: string;
  /** When git first recorded the file, as ISO 8601. */
  added: string | null;
}

export interface EgressRoute {
  by: string;
  to: string;
  what: string;
}

export interface MemoryAbout {
  sources: MemorySource[];
  profile: MemoryPage & { cap: number };
  egress: EgressRoute[];
}

export interface MemoryLogEntry {
  time: string;
  action: string;
  path: string;
}

/** The save was made from a version of the page that is no longer the current one. */
export class StaleError extends Error {}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} failed: ${response.status}`);
  return response.json();
}

// Paths are sent segment by segment, so a name with a space or a "#" still reaches its file.
const encodePath = (path: string) => path.split("/").map(encodeURIComponent).join("/");

export const memoryApi = {
  pages: () => getJson<MemoryPageInfo[]>("/api/memory/pages"),
  page: (path: string) => getJson<MemoryPage>(`/api/memory/pages/${encodePath(path)}`),
  about: () => getJson<MemoryAbout>("/api/memory/about"),
  source: (path: string) => getJson<{ path: string; text: string }>(`/api/memory/sources/${encodePath(path)}`),
  log: () => getJson<MemoryLogEntry[]>("/api/memory/log"),

  /** Save the operator's edit, made from `version`. */
  async save(path: string, text: string, version: string): Promise<MemoryPage> {
    const response = await fetch(`/api/memory/pages/${encodePath(path)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, version }),
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 409) throw new StaleError(body.detail ?? "The page changed since you opened it.");
    if (!response.ok) throw new Error(body.detail ?? `save failed (${response.status})`);
    return body;
  },
};
