/** The server's HTTP API, as the console uses it. */

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
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

  /** The conversation so far: the operator's messages and the replies. */
  async history(): Promise<HistoryMessage[]> {
    const response = await fetch("/api/history");
    if (!response.ok) throw new Error(`history failed: ${response.status}`);
    return response.json();
  },
};
