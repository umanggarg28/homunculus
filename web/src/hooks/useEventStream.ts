import { useEffect, useRef, useState } from "react";
import { eventStreamUrl } from "@/lib/api";
import type { FeedEvent } from "@/lib/types";

// How many events the shared singleton remembers. Components mounting
// after the initial tail still see the recent history.
const SHARED_BUFFER_MAX = 500;
// Keep a sliding window of recently-seen identity keys to drop duplicates
// across EventSource reconnects (the server re-sends the last 50 lines
// as initial backfill every time a stream opens).
const SEEN_KEYS_MAX = 800;

function eventKey(e: FeedEvent): string {
  // Truncate text/args/result because the server itself truncates long
  // payloads with `(+N chars)`; matching on the first 120 chars is
  // sufficient to dedupe initial-tail replay while remaining cheap.
  const tail =
    e.text?.slice(0, 120) ??
    e.args?.slice(0, 120) ??
    e.result?.slice(0, 120) ??
    e.model ??
    "";
  return `${e.ts}|${e.service}|${e.event}|${e.name ?? ""}|${tail}`;
}

const shared = {
  source: null as EventSource | null,
  buffer: [] as FeedEvent[],
  seenKeys: new Set<string>(),
  seenKeyOrder: [] as string[],
  subscribers: new Set<(event: FeedEvent) => void>(),
  connectedSubscribers: new Set<(connected: boolean) => void>(),
};

function rememberKey(key: string) {
  shared.seenKeys.add(key);
  shared.seenKeyOrder.push(key);
  if (shared.seenKeyOrder.length > SEEN_KEYS_MAX) {
    const dropped = shared.seenKeyOrder.shift();
    if (dropped !== undefined) shared.seenKeys.delete(dropped);
  }
}

function ensureSharedSource() {
  if (shared.source) return;
  const src = new EventSource(eventStreamUrl());
  shared.source = src;
  src.onopen = () => {
    shared.connectedSubscribers.forEach((fn) => fn(true));
  };
  src.onerror = () => {
    shared.connectedSubscribers.forEach((fn) => fn(false));
  };
  src.onmessage = (e) => {
    try {
      const evt = JSON.parse(e.data) as FeedEvent;
      const key = eventKey(evt);
      if (shared.seenKeys.has(key)) return; // dedupe replay
      rememberKey(key);
      shared.buffer.push(evt);
      if (shared.buffer.length > SHARED_BUFFER_MAX) {
        shared.buffer = shared.buffer.slice(-SHARED_BUFFER_MAX);
      }
      shared.subscribers.forEach((fn) => fn(evt));
    } catch {
      /* malformed event — ignore */
    }
  };
}

/** Subscribe to the /events SSE endpoint and keep the last `maxEvents`
 * in state. New subscribers receive the singleton's buffered history
 * on mount so navigating between pages does not lose the trace. */
export function useEventStream(maxEvents = 200): {
  events: FeedEvent[];
  connected: boolean;
} {
  const [events, setEvents] = useState<FeedEvent[]>(() =>
    shared.buffer.slice(-maxEvents),
  );
  const [connected, setConnected] = useState(false);
  const maxEventsRef = useRef(maxEvents);
  maxEventsRef.current = maxEvents;

  useEffect(() => {
    ensureSharedSource();

    // Hydrate from the singleton's buffer on (re)mount — important for
    // tab switches via React Router, where this component just mounted
    // but events have already been streaming into the singleton.
    if (shared.buffer.length > 0) {
      setEvents(shared.buffer.slice(-maxEventsRef.current));
    }

    const onEvent = (evt: FeedEvent) => {
      setEvents((prev) => {
        const next = [...prev, evt];
        return next.length > maxEventsRef.current
          ? next.slice(-maxEventsRef.current)
          : next;
      });
    };
    const onConnected = (value: boolean) => setConnected(value);
    shared.subscribers.add(onEvent);
    shared.connectedSubscribers.add(onConnected);
    if (shared.source?.readyState === EventSource.OPEN) setConnected(true);

    return () => {
      shared.subscribers.delete(onEvent);
      shared.connectedSubscribers.delete(onConnected);
    };
  }, []);

  return { events, connected };
}
