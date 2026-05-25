import { useEffect, useRef, useState } from "react";
import type { RobotState } from "./HomunculusRobot";

/** ASCII face character — option #2 from the design conversation.
 *
 *  A purely typographic stand-in for the canvas robot. The face is
 *  rendered in monospace, swaps per state, and has occasional
 *  micro-animations (blink, breath, mouth twitch). Speech bubbles
 *  pop upward (or downward) like the canvas version. When idle
 *  long enough the eyes close and animated z's drift upward — a
 *  proper sleep indicator, not text glued to the face. */

interface Props {
  state: RobotState;
  /** Optional speech bubble text to display above (or below) the face. */
  bubble?: string | null;
  /** Optional bubble color override (defaults to phosphor). */
  bubbleColor?: string;
  /** Where the bubble pops — `"up"` (default) or `"down"`. */
  bubbleDirection?: "up" | "down";
  /** Face character height — controls overall scale. Default 28px. */
  fontSize?: number;
  /** Show the small "STATE · IDLE" sublabel under the face. */
  showLabel?: boolean;
  /** Called when the user mouses over the face. */
  onMouseEnter?: () => void;
}

/** Two-frame face animation per state. The renderer alternates
 *  between [0] and [1] every ~450ms to give micro-life. */
const FACES: Record<RobotState, [string, string]> = {
  boot:       ["(•_•)", "(◉_◉)"],
  idle:       ["(◉_◉)", "(-_-)"],
  listening:  ["(◉‿◉)", "(◕‿◕)"],
  thinking:   ["(@_@)", "(○_○)"],
  working:    ["[>_<]", "[>‿<]"],
  responding: ["(◠‿◠)", "(◡‿◡)"],
  success:    ["\\(^_^)/", "\\(^o^)/"],
  error:      ["(×_×)", "(╥_╥)"],
};

/** Sleep face — fully closed eyes. Used when idle > 60s. */
const SLEEP_FACE: [string, string] = ["(˘_˘)", "(_ _)"];

const STATE_LABEL: Record<RobotState, string> = {
  boot: "BOOTING",
  idle: "IDLE",
  listening: "LISTENING",
  thinking: "THINKING",
  working: "WORKING",
  responding: "SPEAKING",
  success: "DONE",
  error: "ERROR",
};

const STATE_COLOR: Record<RobotState, string> = {
  boot: "var(--color-text-dim)",
  idle: "var(--color-text)",
  listening: "var(--color-info)",
  thinking: "var(--color-accent)",
  working: "var(--color-warning)",
  responding: "var(--color-accent)",
  success: "var(--color-accent)",
  error: "var(--color-danger)",
};

export function AsciiFace({
  state,
  bubble,
  bubbleColor,
  bubbleDirection = "up",
  fontSize = 28,
  showLabel = true,
  onMouseEnter,
}: Props) {
  const [frame, setFrame] = useState(0);
  const [asleep, setAsleep] = useState(false);
  const lastBlinkRef = useRef<number>(performance.now());

  // Two-frame swap loop — blinks for `idle`, faster swap for others.
  useEffect(() => {
    let raf = 0;
    const tick = (now: number) => {
      const since = now - lastBlinkRef.current;
      const blinkGap = state === "idle" ? 4500 : 450;
      const blinkDur = state === "idle" ? 150 : 380;
      if (since > blinkGap) setFrame(1);
      if (since > blinkGap + blinkDur) {
        setFrame(0);
        lastBlinkRef.current = now;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state]);

  // After 60s of `idle` the face goes to sleep — eyes closed +
  // drifting z's. Any state change wakes it instantly.
  useEffect(() => {
    if (state !== "idle") {
      setAsleep(false);
      return;
    }
    const t = setTimeout(() => setAsleep(true), 60_000);
    return () => clearTimeout(t);
  }, [state]);

  const faceFrames = asleep ? SLEEP_FACE : FACES[state];
  const face = faceFrames[frame];
  const color = asleep ? "var(--color-text-muted)" : STATE_COLOR[state];

  return (
    <div
      style={{
        position: "relative",
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 6,
      }}
    >
      {/* Speech bubble (above) */}
      {bubble && bubbleDirection === "up" && (
        <SpeechBubble text={bubble} color={bubbleColor} direction="up" />
      )}

      {/* The face itself + sleep z's */}
      <div
        onMouseEnter={onMouseEnter}
        style={{
          position: "relative",
          fontFamily: "var(--font-mono)",
          fontSize,
          fontWeight: 700,
          color,
          textShadow: `0 0 14px ${color}`,
          letterSpacing: "0.04em",
          lineHeight: 1,
          padding: "4px 8px",
          transition: "color 220ms, text-shadow 220ms",
          cursor: onMouseEnter ? "pointer" : "default",
          userSelect: "none",
        }}
      >
        {face}
        {asleep && <SleepZs fontSize={fontSize} />}
      </div>

      {/* Speech bubble (below) */}
      {bubble && bubbleDirection === "down" && (
        <SpeechBubble text={bubble} color={bubbleColor} direction="down" />
      )}

      {showLabel && (
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            letterSpacing: "0.22em",
            color: "var(--color-text-faint)",
            textTransform: "uppercase",
          }}
        >
          ── {asleep ? "ASLEEP" : STATE_LABEL[state]}
        </div>
      )}
    </div>
  );
}

/** Three z's drifting upward + fading. Staggered phases so they feel
 *  like a sleep stream rather than a static "zzz" stamp. */
function SleepZs({ fontSize }: { fontSize: number }) {
  return (
    <span
      aria-hidden
      style={{
        position: "absolute",
        right: -2,
        top: -fontSize * 0.5,
        pointerEvents: "none",
        fontFamily: "var(--font-mono)",
        fontSize: fontSize * 0.42,
        lineHeight: 1,
        color: "var(--color-text-dim)",
      }}
    >
      <span style={{ position: "absolute", animation: "zz-drift 2.4s ease-out infinite", animationDelay: "0s",   opacity: 0 }}>z</span>
      <span style={{ position: "absolute", animation: "zz-drift 2.4s ease-out infinite", animationDelay: "0.8s", opacity: 0, left: 5, fontSize: "0.85em" }}>z</span>
      <span style={{ position: "absolute", animation: "zz-drift 2.4s ease-out infinite", animationDelay: "1.6s", opacity: 0, left: 10, fontSize: "0.72em" }}>z</span>
      <style>{`
        @keyframes zz-drift {
          0%   { transform: translate(0, 4px)   rotate(-4deg); opacity: 0;   }
          15%  { opacity: 0.9; }
          70%  { opacity: 0.6; }
          100% { transform: translate(8px, -14px) rotate(8deg); opacity: 0;  }
        }
      `}</style>
    </span>
  );
}

function SpeechBubble({
  text,
  color,
  direction = "up",
}: {
  text: string;
  color?: string;
  direction?: "up" | "down";
}) {
  const c = color ?? "var(--color-text)";
  const isUp = direction === "up";
  return (
    <div
      style={{
        position: "absolute",
        ...(isUp
          ? { bottom: "calc(100% + 4px)" }
          : { top: "calc(100% + 4px)" }),
        left: "50%",
        transform: "translateX(-50%)",
        background: "rgba(8,12,8,0.96)",
        border: `1px solid ${c}`,
        boxShadow: `0 0 14px ${c}`,
        padding: "5px 10px",
        fontFamily: "var(--font-mono)",
        fontSize: 10.5,
        letterSpacing: "0.08em",
        color: c,
        whiteSpace: "nowrap",
        animation: "ascii-bubble-pop 220ms cubic-bezier(0.34, 1.56, 0.64, 1)",
        pointerEvents: "none",
        zIndex: 50,
      }}
    >
      <span>{text}</span>
      {/* Tail */}
      <span
        style={{
          position: "absolute",
          ...(isUp
            ? { bottom: -6, borderTop: `6px solid ${c}` }
            : { top: -6,    borderBottom: `6px solid ${c}` }),
          left: "50%",
          transform: "translateX(-50%)",
          width: 0,
          height: 0,
          borderLeft: "5px solid transparent",
          borderRight: "5px solid transparent",
        }}
      />
      <span
        style={{
          position: "absolute",
          ...(isUp
            ? { bottom: -5, borderTop: "5px solid rgba(8,12,8,0.96)" }
            : { top: -5,    borderBottom: "5px solid rgba(8,12,8,0.96)" }),
          left: "50%",
          transform: "translateX(-50%)",
          width: 0,
          height: 0,
          borderLeft: "4px solid transparent",
          borderRight: "4px solid transparent",
        }}
      />
      <style>{`
        @keyframes ascii-bubble-pop {
          0%   { opacity: 0; transform: translateX(-50%) translateY(${isUp ? "6px" : "-6px"}) scale(0.85); }
          100% { opacity: 1; transform: translateX(-50%) translateY(0) scale(1); }
        }
      `}</style>
    </div>
  );
}
