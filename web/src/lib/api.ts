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
}

async function decide(id: string, action: "approve" | "deny" | "done", note?: string): Promise<Approval> {
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
  /** Send a message. The reply streams back as server-sent events:
   *  `delta` (text), `tool` (one tool step), then `done` or `error`. */
  chat(message: string, signal?: AbortSignal): Promise<Response> {
    return fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
      signal,
    });
  },

  /** Pending requests first, then the most recently decided ones. */
  async approvals(): Promise<Approval[]> {
    const response = await fetch("/api/approvals");
    if (!response.ok) throw new Error(`approvals failed: ${response.status}`);
    return response.json();
  },

  /** Run exactly the requested action; the agent is told the result. */
  approve: (id: string) => decide(id, "approve"),
  /** Decline; nothing runs and the agent is told, with the reason if given. */
  deny: (id: string, reason?: string) => decide(id, "deny", reason),
  /** Mark a hand-off as done by the operator; the agent is told. */
  done: (id: string, note?: string) => decide(id, "done", note),

  /** The conversation so far: the operator's messages and the replies. */
  async history(): Promise<HistoryMessage[]> {
    const response = await fetch("/api/history");
    if (!response.ok) throw new Error(`history failed: ${response.status}`);
    return response.json();
  },
};
