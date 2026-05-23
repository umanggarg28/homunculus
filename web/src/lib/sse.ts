// Pure SSE parsing helpers — no React, no DOM dependency beyond fetch.
//
// SSE format: events separated by blank lines, each event is one or more
// lines like `data: <payload>` or `event: <name>`. The browser's native
// EventSource handles this for GET requests, but for our streaming POST
// (/api/chat/send) we read the response body manually and parse it ourselves.

export interface ParsedSseEvent {
  event?: string;
  data: string;
}

export function decodeJsonSseData(data: string): string {
  try {
    const decoded = JSON.parse(data);
    return typeof decoded === "string" ? decoded : data;
  } catch {
    return data;
  }
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
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    else if (line.startsWith("event:")) event = line.slice(6).trim();
  }
  if (!dataLines.length && !event) return null;
  return { event, data: dataLines.join("\n") };
}
