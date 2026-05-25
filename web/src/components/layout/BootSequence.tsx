import { useEffect, useState } from "react";
import { DotMatrixWordmark } from "./DotMatrixWordmark";

/** One-shot terminal boot sequence shown only on the FIRST page load
 *  per browser tab. Stores a sentinel in sessionStorage so subsequent
 *  navigations skip it. Total runtime ~1.6s, then a 250ms fade out.
 *
 *  Renders as a full-screen overlay above everything (z-9999),
 *  printing six terminal lines in sequence with phosphor type. */

const LINES: { text: string; delay: number }[] = [
  { text: "$ homunculus --boot",          delay: 0 },
  { text: "[OK] event stream attached",   delay: 220 },
  { text: "[OK] memory mounted · ~/.claude/projects/…", delay: 400 },
  { text: "[OK] tool registry · 11 mounted", delay: 580 },
  { text: "[OK] robot online · cream phosphor build", delay: 760 },
  { text: "[OK] sse ▸ /events",            delay: 940 },
  { text: "[OK] ready · awaiting prompt", delay: 1180 },
];

const FINAL_DELAY = 1500;
const FADE_MS = 250;
const STORAGE_KEY = "homunculus_booted_v1";

export function BootSequence() {
  const [show, setShow] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      return sessionStorage.getItem(STORAGE_KEY) !== "1";
    } catch {
      return false;
    }
  });
  const [printed, setPrinted] = useState(0);
  const [fading, setFading] = useState(false);

  useEffect(() => {
    if (!show) return;
    const timers: ReturnType<typeof setTimeout>[] = [];
    LINES.forEach((_, i) => {
      timers.push(setTimeout(() => setPrinted((p) => Math.max(p, i + 1)), LINES[i].delay));
    });
    timers.push(setTimeout(() => setFading(true), FINAL_DELAY));
    timers.push(setTimeout(() => {
      try { sessionStorage.setItem(STORAGE_KEY, "1"); } catch { /* ignore */ }
      setShow(false);
    }, FINAL_DELAY + FADE_MS));
    return () => timers.forEach(clearTimeout);
  }, [show]);

  if (!show) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        background: "var(--color-bg)",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
        gap: 28,
        fontFamily: "var(--font-mono)",
        opacity: fading ? 0 : 1,
        transition: `opacity ${FADE_MS}ms ease-out`,
        pointerEvents: fading ? "none" : "auto",
      }}
    >
      <DotMatrixWordmark text="HOMUNCULUS" dotSize={5} gap={2} />

      <div
        style={{
          width: "min(640px, 88vw)",
          minHeight: 200,
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          lineHeight: 1.7,
          color: "var(--color-text-dim)",
          padding: "18px 22px",
          border: "1px solid var(--color-border)",
          background: "rgba(0,0,0,0.45)",
        }}
      >
        {LINES.slice(0, printed).map((line, i) => {
          const isOk = line.text.startsWith("[OK]");
          const isCmd = line.text.startsWith("$");
          return (
            <div
              key={i}
              style={{
                color: isCmd ? "var(--color-accent)" : isOk ? "var(--color-text)" : "var(--color-text-muted)",
                whiteSpace: "pre-wrap",
              }}
            >
              {isOk ? (
                <>
                  <span style={{ color: "var(--color-accent)" }}>[OK]</span>
                  <span>{line.text.slice(4)}</span>
                </>
              ) : (
                <span>{line.text}</span>
              )}
            </div>
          );
        })}
        {/* Blinking cursor under last line */}
        <div style={{ marginTop: 4 }}>
          <span style={{ color: "var(--color-text-muted)" }}>&gt; </span>
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 13,
              background: "var(--color-accent)",
              verticalAlign: "middle",
              animation: "boot-cursor 1s steps(2) infinite",
            }}
          />
        </div>
      </div>

      <style>{`
        @keyframes boot-cursor { 50% { opacity: 0; } }
      `}</style>
    </div>
  );
}
