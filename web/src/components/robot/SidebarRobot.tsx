import { useEffect, useRef, useState } from "react";
import { HomunculusRobot } from "./HomunculusRobot";
import { useRobotState } from "@/hooks/useRobotState";
import { useEventStream } from "@/hooks/useEventStream";
import type { FeedEvent } from "@/lib/types";

/** Frameless robot pinned to the bottom of the sidebar, with a
 *  contextual speech bubble that pops upward into the nav area.
 *
 *  The bubble is the character. It says short things in the agent's
 *  voice based on real activity:
 *    LLM call         → "thinking…"
 *    tool_call X      → "calling X…"
 *    tool_result OK   → "got it."
 *    tool_result ERR  → "ugh."
 *    memory_*         → "noted."
 *    assistant_reply  → "there." / "done."
 *    user_message     → "you said?"
 *    long idle        → "…zzz"
 *    mouseover        → "hi" / "yeah?"
 *
 *  Each bubble lasts ~3s. New events cancel the current bubble and
 *  show their own. */

interface BubbleMsg {
  id: number;
  text: string;
  // amber for warnings, red for errors, otherwise cream/phosphor
  color?: string;
  /** ms until the bubble auto-dismisses. */
  durationMs?: number;
}

const HI_PHRASES = ["hi.", "yeah?", "hm?", "yes?", "what's up?"];
const IDLE_PHRASES = ["…zzz", "still here.", "anything?", "…thinking…"];

export function SidebarRobot() {
  const robotState = useRobotState();
  const { events } = useEventStream(30);
  const [bubble, setBubble] = useState<BubbleMsg | null>(null);
  const lastEventKeyRef = useRef<string>("");
  const bubbleIdRef = useRef(0);
  const lastSpokeRef = useRef<number>(0);
  const longIdleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Speak a new line, replacing any current bubble.
  const speak = (text: string, opts?: { color?: string; durationMs?: number }) => {
    bubbleIdRef.current += 1;
    const id = bubbleIdRef.current;
    const durationMs = opts?.durationMs ?? 2800;
    setBubble({ id, text, color: opts?.color, durationMs });
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
    if (age > 4000) return; // backfilled / stale — don't bubble

    const line = bubbleForEvent(last);
    if (line) speak(line.text, { color: line.color, durationMs: line.durationMs });
  }, [events]);

  // ── Long-idle bubble — fires every ~90s of silence ─────────────
  useEffect(() => {
    if (longIdleTimerRef.current) clearTimeout(longIdleTimerRef.current);
    const lastEvent = events[events.length - 1];
    const lastAge = lastEvent ? Date.now() - new Date(lastEvent.ts).getTime() : Infinity;
    if (lastAge < 60_000) return; // wait until we've been idle for a minute
    longIdleTimerRef.current = setTimeout(() => {
      // Only fire if nothing's spoken recently
      if (Date.now() - lastSpokeRef.current > 45_000) {
        speak(IDLE_PHRASES[Math.floor(Math.random() * IDLE_PHRASES.length)]);
      }
    }, 25_000 + Math.random() * 30_000);
    return () => { if (longIdleTimerRef.current) clearTimeout(longIdleTimerRef.current); };
  }, [events]);

  // ── Mouseover → direct address ─────────────────────────────────
  const onEnter = () => {
    const phrase = HI_PHRASES[Math.floor(Math.random() * HI_PHRASES.length)];
    speak(phrase, { durationMs: 1800 });
  };

  return (
    <div
      style={{
        position: "relative",
        padding: "8px 12px 14px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
      }}
    >
      {/* Bubble — pops above the robot, breaks out of clip via z. */}
      {bubble && <SpeechBubble key={bubble.id} text={bubble.text} color={bubble.color} />}

      <div
        onMouseEnter={onEnter}
        style={{
          width: 104,
          height: 104,
          cursor: "pointer",
        }}
      >
        <HomunculusRobot
          state={robotState}
          detail="mid"
          palette="cream"
          filled
          noDust
          style={{ width: "100%", height: "100%", display: "block" }}
        />
      </div>
    </div>
  );
}

function SpeechBubble({ text, color }: { text: string; color?: string }) {
  const c = color ?? "var(--color-text)";
  return (
    <div
      style={{
        position: "absolute",
        bottom: "calc(100% - 12px)",
        left: "50%",
        transform: "translateX(-50%)",
        background: "rgba(8,12,8,0.96)",
        border: `1px solid ${c}`,
        boxShadow: `0 0 14px ${color ? c : "rgba(232,224,200,0.25)"}`,
        padding: "5px 10px",
        fontFamily: "var(--font-mono)",
        fontSize: 10.5,
        letterSpacing: "0.08em",
        color: c,
        whiteSpace: "nowrap",
        animation: "bubble-pop 220ms cubic-bezier(0.34, 1.56, 0.64, 1)",
        pointerEvents: "none",
        zIndex: 10,
      }}
    >
      <span>{text}</span>
      {/* Tail — small triangle pointing down at the robot */}
      <span
        style={{
          position: "absolute",
          bottom: -6,
          left: "50%",
          transform: "translateX(-50%)",
          width: 0,
          height: 0,
          borderLeft: "5px solid transparent",
          borderRight: "5px solid transparent",
          borderTop: `6px solid ${c}`,
        }}
      />
      <span
        style={{
          position: "absolute",
          bottom: -5,
          left: "50%",
          transform: "translateX(-50%)",
          width: 0,
          height: 0,
          borderLeft: "4px solid transparent",
          borderRight: "4px solid transparent",
          borderTop: "5px solid rgba(8,12,8,0.96)",
        }}
      />
      <style>{`
        @keyframes bubble-pop {
          0%   { opacity: 0; transform: translateX(-50%) translateY(6px) scale(0.85); }
          100% { opacity: 1; transform: translateX(-50%) translateY(0)   scale(1);    }
        }
      `}</style>
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
