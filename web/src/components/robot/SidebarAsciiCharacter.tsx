import { useEffect, useRef, useState } from "react";
import { AsciiFace, ANNOYED_FACE, FACES } from "./AsciiFace";
import { useRobotState } from "@/hooks/useRobotState";
import { useEventStream } from "@/hooks/useEventStream";
import type { FeedEvent } from "@/lib/types";

/** ASCII face character mounted at the bottom of the sidebar.
 *  Mirrors the SidebarRobot behavior (SSE-driven bubbles, idle
 *  zzz, mouseover "hi") but renders the character as monospace
 *  text rather than a canvas robot. Designed to fit the brutalist
 *  terminal aesthetic without aesthetic mismatch.
 *
 *  To revert to the canvas robot: in Sidebar.tsx swap the
 *  `<SidebarAsciiCharacter />` mount back to `<SidebarRobot />`.
 *  Both components remain in the tree. */

const HI_PHRASES = ["hi.", "yeah?", "hm?", "yes?", "what's up?", "operator detected."];
const IDLE_PHRASES = ["…zzz", "still here.", "anything?", "…hm.", "anyone?"];
/** Rare idle lines with teeth. The danger aesthetic, one sentence at
 *  a time — drawn ~15% of idle speaks so it stays a glint, not a bit. */
const OMINOUS_PHRASES = [
  "containment holding.",
  "i don't sleep. i wait.",
  "i could do this without you.",
  "all systems nominal. for now.",
  "i remember everything.",
];
const ANNOYED_PHRASES = ["i'm working.", "stop that.", "(sigh)", "noted. again."];

interface Props {
  /** Where the bubble pops relative to the face. */
  direction?: "up" | "down";
  /** Face character size in px. */
  fontSize?: number;
  /** Show the "── STATE" label under the face. */
  showLabel?: boolean;
}

export function SidebarAsciiCharacter({
  direction = "up",
  fontSize = 26,
  showLabel = true,
}: Props = {}) {
  const robotState = useRobotState();
  const { events } = useEventStream(30);
  const [bubble, setBubble] = useState<{ id: number; text: string; color?: string } | null>(null);
  const [override, setOverride] = useState<[string, string] | null>(null);
  const lastEventKeyRef = useRef<string>("");
  const bubbleIdRef = useRef(0);
  const lastSpokeRef = useRef<number>(Date.now());
  const hoverTimesRef = useRef<number[]>([]);
  const overrideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Hold a face override briefly (annoyed glare, victory pose).
  const flashFace = (face: [string, string], ms: number) => {
    if (overrideTimerRef.current) clearTimeout(overrideTimerRef.current);
    setOverride(face);
    overrideTimerRef.current = setTimeout(() => setOverride(null), ms);
  };

  // Speak a new line, replacing any current bubble.
  const speak = (text: string, opts?: { color?: string; durationMs?: number }) => {
    bubbleIdRef.current += 1;
    const id = bubbleIdRef.current;
    const durationMs = opts?.durationMs ?? 2800;
    setBubble({ id, text, color: opts?.color });
    lastSpokeRef.current = Date.now();
    setTimeout(() => {
      setBubble((cur) => (cur && cur.id === id ? null : cur));
    }, durationMs);
  };

  // ── Drive bubbles from SSE events ──────────────────────────────
  useEffect(() => {
    const last = events[events.length - 1];
    if (!last) return;
    const key = `${last.ts}|${last.event}`;
    if (key === lastEventKeyRef.current) return;
    lastEventKeyRef.current = key;
    const age = Date.now() - new Date(last.ts).getTime();
    if (age > 4000) return;

    const line = bubbleForEvent(last);
    if (line) speak(line.text, { color: line.color, durationMs: line.durationMs });
    // A notify that went through is the agent's whole job working —
    // strike the victory pose, not just another "got it."
    if (
      last.event === "tool_result" &&
      (last.name ?? "").toLowerCase() === "notify" &&
      !(typeof last.result === "string" && /^error/i.test(last.result))
    ) {
      flashFace(FACES.success, 2400);
    }
  }, [events]);

  // ── Long-idle bubble — periodic checker that runs *regardless*
  //    of whether `events` re-fires. (The earlier effect-based
  //    version had a bug where it never scheduled the timer during
  //    actual silence.) ───────────────────────────────────────────
  useEffect(() => {
    const id = setInterval(() => {
      const sinceSpoken = Date.now() - lastSpokeRef.current;
      const lastEvent = events[events.length - 1];
      const sinceEvent = lastEvent
        ? Date.now() - new Date(lastEvent.ts).getTime()
        : Infinity;
      // Speak an idle line if both conditions hold:
      //   • at least 45s since last bubble
      //   • at least 60s since last agent event
      // Roll dice each tick so it doesn't feel metronomic.
      if (sinceSpoken > 45_000 && sinceEvent > 60_000 && Math.random() < 0.25) {
        const pool = Math.random() < 0.15 ? OMINOUS_PHRASES : IDLE_PHRASES;
        speak(pool[Math.floor(Math.random() * pool.length)]);
      }
    }, 12_000);
    return () => clearInterval(id);
  }, [events]);

  // ── Mouseover → direct address; poke it 4× in 8s and it glares ──
  const onHover = () => {
    const now = Date.now();
    hoverTimesRef.current = [...hoverTimesRef.current.filter((t) => now - t < 8000), now];
    if (hoverTimesRef.current.length >= 4) {
      hoverTimesRef.current = [];
      flashFace(ANNOYED_FACE, 1800);
      speak(ANNOYED_PHRASES[Math.floor(Math.random() * ANNOYED_PHRASES.length)], {
        color: "var(--color-warning)",
        durationMs: 1800,
      });
      return;
    }
    const phrase = HI_PHRASES[Math.floor(Math.random() * HI_PHRASES.length)];
    speak(phrase, { durationMs: 1800 });
  };

  return (
    <div style={{ position: "relative", display: "inline-flex" }}>
      <AsciiFace
        state={robotState}
        bubble={bubble?.text ?? null}
        bubbleColor={bubble?.color}
        bubbleDirection={direction}
        fontSize={fontSize}
        showLabel={showLabel}
        onMouseEnter={onHover}
        overrideFace={override}
      />
    </div>
  );
}

function bubbleForEvent(e: FeedEvent): { text: string; color?: string; durationMs?: number } | null {
  switch (e.event) {
    case "llm_call":
      return { text: "thinking…", durationMs: 2200 };
    case "tool_call": {
      const name = (e.name ?? "tool").toLowerCase();
      return { text: `calling ${name}…`, color: "var(--color-warning)", durationMs: 2400 };
    }
    case "tool_result": {
      const isError = typeof e.result === "string" && /^error/i.test(e.result);
      if (isError) return { text: "ugh.", color: "var(--color-danger)", durationMs: 3000 };
      if ((e.name ?? "").toLowerCase() === "notify") {
        return { text: "delivered ✓", color: "var(--color-accent)", durationMs: 3000 };
      }
      return { text: "got it.", color: "var(--color-accent)", durationMs: 1800 };
    }
    case "assistant_reply":
      return { text: "there.", color: "var(--color-accent)", durationMs: 1800 };
    case "user_message":
      return { text: "hmm…", durationMs: 1400 };
    default:
      if (typeof e.event === "string" && e.event.startsWith("memory")) {
        return { text: "noted.", color: "var(--color-accent)", durationMs: 1800 };
      }
      return null;
  }
}
