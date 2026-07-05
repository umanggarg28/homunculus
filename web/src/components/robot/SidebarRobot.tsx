import { useEffect, useRef, useState } from "react";
import { HomunculusRobot } from "./HomunculusRobot";
import { useRobotState } from "@/hooks/useRobotState";
import { useEventStream } from "@/hooks/useEventStream";
import { useAgentPaused } from "@/hooks/useAgentPaused";
import type { FeedEvent } from "@/lib/types";

/** The sidebar presence: pixel robot in a unit card, alive.
 *
 *  Behavior layer (desktop-pet patterns, clawd-on-desk style):
 *  - body leans toward the cursor; close approach makes it perk up
 *  - ~3% of idle "blinks" corrupt into a 140ms red glitch + SIGNAL LOST
 *  - poke it 4× in 8s and it shakes, glares and complains
 *  - a delivered notify() earns a victory hop + "delivered ✓"
 *  - idle mutterings occasionally come from an ominous pool
 *  - kill switch engaged → unit goes dark, label reads HALTED
 *
 *  The dialogue box is a terminal line: typewriter reveal with a
 *  blinking block cursor, clipped corner, phosphor glow.
 */

interface BubbleMsg {
  id: number;
  text: string;
  // amber for warnings, red for errors, otherwise cream/phosphor
  color?: string;
  /** ms until the bubble auto-dismisses. */
  durationMs?: number;
}

const HI_PHRASES = ["hi.", "yeah?", "hm?", "yes?", "what's up?", "operator detected."];
const IDLE_PHRASES = ["…zzz", "still here.", "anything?", "…thinking…"];
/** Rare idle lines with teeth — drawn ~15% of idle speaks. A glint,
 *  not a bit: the unit is calm green until it occasionally isn't. */
const OMINOUS_PHRASES = [
  "containment holding.",
  "i don't sleep. i wait.",
  "i could do this without you.",
  "all systems nominal. for now.",
  "i remember everything.",
];
const ANNOYED_PHRASES = ["i'm working.", "stop that.", "(sigh)", "noted. again."];

const STATE_LABELS: Record<string, string> = {
  idle: "ALIVE",
  boot: "INIT",
  listening: "LISTENING",
  thinking: "THINKING",
  working: "WORKING",
  responding: "RESPONDING",
  success: "DONE",
  error: "FAULT",
};

export function SidebarRobot() {
  const robotState = useRobotState();
  const paused = useAgentPaused();
  const { events } = useEventStream(30);
  const [bubble, setBubble] = useState<BubbleMsg | null>(null);
  const [glitching, setGlitching] = useState(false);
  const [annoyed, setAnnoyed] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [lean, setLean] = useState({ rot: 0, near: false });
  const cardRef = useRef<HTMLDivElement>(null);
  const lastEventKeyRef = useRef<string>("");
  const bubbleIdRef = useRef(0);
  const lastSpokeRef = useRef<number>(0);
  const longIdleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hoverTimesRef = useRef<number[]>([]);

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
    // A notify that went through is the whole job working — victory
    // hop, not another "got it."
    if (
      last.event === "tool_result" &&
      (last.name ?? "").toLowerCase() === "notify" &&
      !(typeof last.result === "string" && /^error/i.test(last.result))
    ) {
      setCelebrating(true);
      setTimeout(() => setCelebrating(false), 750);
    }
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
        const pool = Math.random() < 0.15 ? OMINOUS_PHRASES : IDLE_PHRASES;
        speak(pool[Math.floor(Math.random() * pool.length)]);
      }
    }, 25_000 + Math.random() * 30_000);
    return () => { if (longIdleTimerRef.current) clearTimeout(longIdleTimerRef.current); };
  }, [events]);

  // ── Cursor tracking — lean toward the pointer, perk up close ───
  useEffect(() => {
    if (paused) { setLean({ rot: 0, near: false }); return; }
    let raf = 0;
    let last: MouseEvent | null = null;
    const onMove = (e: MouseEvent) => {
      last = e;
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (!last || !cardRef.current) return;
        const r = cardRef.current.getBoundingClientRect();
        const dx = last.clientX - (r.left + r.width / 2);
        const dy = last.clientY - (r.top + r.height / 2);
        setLean({
          rot: Math.max(-6, Math.min(6, dx / 70)),
          near: Math.hypot(dx, dy) < 140,
        });
      });
    };
    window.addEventListener("mousemove", onMove);
    return () => { window.removeEventListener("mousemove", onMove); if (raf) cancelAnimationFrame(raf); };
  }, [paused]);

  // ── Rare glitch — ~3% chance every 4.5s while idle ──────────────
  useEffect(() => {
    if (robotState !== "idle" || paused) return;
    const t = setInterval(() => {
      if (Math.random() < 0.03) {
        setGlitching(true);
        setTimeout(() => setGlitching(false), 180);
      }
    }, 4500);
    return () => clearInterval(t);
  }, [robotState, paused]);

  // ── Mouseover → direct address; poke it 4× in 8s and it glares ──
  const onEnter = () => {
    if (paused) { speak("halted by operator.", { color: "var(--color-danger)", durationMs: 2200 }); return; }
    const now = Date.now();
    hoverTimesRef.current = [...hoverTimesRef.current.filter((t) => now - t < 8000), now];
    if (hoverTimesRef.current.length >= 4) {
      hoverTimesRef.current = [];
      setAnnoyed(true);
      setTimeout(() => setAnnoyed(false), 800);
      speak(ANNOYED_PHRASES[Math.floor(Math.random() * ANNOYED_PHRASES.length)], {
        color: "var(--color-warning)",
        durationMs: 1800,
      });
      return;
    }
    const phrase = HI_PHRASES[Math.floor(Math.random() * HI_PHRASES.length)];
    speak(phrase, { durationMs: 1800 });
  };

  const stateLabel = paused
    ? "HALTED"
    : glitching
      ? "SIGNAL LOST"
      : STATE_LABELS[robotState] ?? robotState.toUpperCase();
  const pipColor = paused || glitching || robotState === "error"
    ? "var(--color-danger)"
    : "var(--color-accent)";

  return (
    <div
      style={{
        position: "relative",
        padding: "8px 12px 12px",
      }}
    >
      {/* Bubble — pops above, breaks out of clip via z. */}
      {bubble && <SpeechBubble key={bubble.id} text={bubble.text} color={bubble.color} />}

      {/* Presence card — robot + name/state */}
      <div
        ref={cardRef}
        onMouseEnter={onEnter}
        className={`hm-screen-well ${
          glitching ? "hm-glitching" : annoyed ? "hm-robot-annoyed" : celebrating ? "hm-robot-celebrate" : ""
        }`}
        style={{
          border: `1px solid ${paused ? "color-mix(in srgb, var(--color-danger) 50%, transparent)" : "var(--color-border)"}`,
          // A CRT screen inside the chassis — the deepest black in the
          // app, so the panel reads as physically recessed and lit.
          background: "var(--color-screen)",
          padding: "10px",
          display: "grid",
          gridTemplateColumns: "50px 1fr",
          gap: 10,
          alignItems: "center",
          cursor: "pointer",
        }}
      >
        <div
          style={{
            width: 50,
            height: 58,
            transform: paused ? "none" : `rotate(${lean.rot}deg) scale(${lean.near ? 1.07 : 1})`,
            transformOrigin: "50% 80%",
            transition: "transform 360ms ease-out, filter 400ms",
            filter: paused ? "grayscale(0.85) brightness(0.55)" : "none",
          }}
        >
          <HomunculusRobot
            state={paused ? "idle" : robotState}
            detail="mid"
            palette="phosphor"
            filled
            noDust
            style={{ width: "100%", height: "100%", display: "block" }}
          />
        </div>
        <div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              letterSpacing: "0.14em",
              color: paused ? "var(--color-text-muted)" : "var(--color-accent)",
              fontWeight: 600,
              textTransform: "uppercase",
            }}
          >
            HOMUNCULUS
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              letterSpacing: "0.10em",
              color: paused || glitching ? "var(--color-danger)" : "var(--color-text-muted)",
              textTransform: "uppercase",
              display: "flex",
              alignItems: "center",
              gap: 5,
              marginTop: 3,
            }}
          >
            <span
              style={{
                width: 4,
                height: 4,
                borderRadius: "50%",
                background: pipColor,
                boxShadow: `0 0 5px ${pipColor}`,
                display: "inline-block",
                animation: paused ? "none" : "sidebar-pip 1.6s ease-in-out infinite",
              }}
            />
            <style>{`@keyframes sidebar-pip { 0%,100%{opacity:1} 50%{opacity:.3} }`}</style>
            {stateLabel}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Terminal one-liner: typewriter reveal with a blinking block cursor.
 *  Otherwise deliberately plain — at 10px, texture and cut corners
 *  read as clutter, not craft (tried both; reverted). */
function SpeechBubble({ text, color }: { text: string; color?: string }) {
  const c = color ?? "var(--color-text)";
  const [shown, setShown] = useState(0);

  useEffect(() => {
    setShown(0);
    let i = 0;
    const t = setInterval(() => {
      i += 1;
      setShown(i);
      if (i >= text.length) clearInterval(t);
    }, 16);
    return () => clearInterval(t);
  }, [text]);

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
      <span>{text.slice(0, shown)}</span>
      <span
        aria-hidden
        style={{
          display: "inline-block",
          width: 6,
          height: 11,
          marginLeft: 2,
          verticalAlign: "-1px",
          background: c,
          animation: shown >= text.length ? "hm-cursor-blink 0.9s steps(1) infinite" : "none",
        }}
      />
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
      <style>{`
        @keyframes bubble-pop {
          0%   { opacity: 0; transform: translateX(-50%) translateY(6px) scale(0.85); }
          100% { opacity: 1; transform: translateX(-50%) translateY(0)   scale(1);    }
        }
        @keyframes hm-cursor-blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }
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
