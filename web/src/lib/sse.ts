// Pure SSE parsing helpers: no React, no DOM dependency beyond fetch.
//
// SSE format: events separated by blank lines, each event is one or more
// lines like `id: <number>`, `event: <name>` or `data: <payload>`. The id is
// what a reconnecting reader sends back as `Last-Event-ID`. The browser's native
// EventSource handles this for GET requests, but for our streaming POST
// (/api/chat) we read the response body manually and parse it ourselves.

export interface ParsedSseEvent {
  id?: string;
  event?: string;
  data: string;
}

/** Walk a buffered chunk of SSE text and yield complete events.
 * Returns parsed events and the leftover (incomplete) trailing buffer. */
export function parseSseChunks(buffer: string): {
  events: ParsedSseEvent[];
  remainder: string;
} {
  const events: ParsedSseEvent[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n");
  const parts = normalized.split(/\n\n+/);
  const remainder = parts.pop() ?? "";

  for (const part of parts) {
    const parsed = parseSingleEvent(part);
    if (parsed) events.push(parsed);
  }
  return { events, remainder };
}

function parseSingleEvent(block: string): ParsedSseEvent | null {
  const lines = block.split("\n");
  let event: string | undefined;
  let id: string | undefined;
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    else if (line.startsWith("event:")) event = line.slice(6).trim();
    // The standard drops one space after the colon, and ignores an id holding NULL.
    else if (line.startsWith("id:")) {
      const value = line.slice(3).replace(/^ /, "");
      if (!value.includes("\0")) id = value;
    }
  }
  if (!dataLines.length && !event) return null;
  return { id, event, data: dataLines.join("\n") };
}
