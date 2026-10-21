import { useEffect, useState } from "react";
import { DotMatrixWordmark } from "./DotMatrixWordmark";
import { api } from "@/lib/api";

/** One-shot terminal boot sequence, shown on the first page load in a tab.
 *  A sessionStorage sentinel skips it on later navigations.
 *
 *  Its status line is the result of a real request to the server, so the
 *  overlay never reports a check it did not make. */

const COMMAND = "$ homunculus --boot";
const MIN_CHECK_MS = 300;
const HOLD_MS = 700;
const FADE_MS = 250;
const STORAGE_KEY = "homunculus_booted_v1";

async function checkServer(): Promise<string> {
  try {
    const history = await api.history();
    const turns = history.filter((m) => m.role === "user").length;
    return `[OK] server reachable · ${turns} turn${turns === 1 ? "" : "s"} in this conversation`;
  } catch {
    return "[!!] server unreachable · start it with uv run homunculus";
  }
}

export function BootSequence() {
  const [show, setShow] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      return sessionStorage.getItem(STORAGE_KEY) !== "1";
    } catch {
      return false;
    }
  });
  const [lines, setLines] = useState<string[]>([COMMAND]);
  const [fading, setFading] = useState(false);

  useEffect(() => {
    if (!show) return;
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const started = Date.now();
    checkServer().then((status) => {
      if (cancelled) return;
      const wait = Math.max(0, MIN_CHECK_MS - (Date.now() - started));
      timers.push(setTimeout(() => setLines([COMMAND, status]), wait));
      timers.push(setTimeout(() => setFading(true), wait + HOLD_MS));
      timers.push(setTimeout(() => {
        try { sessionStorage.setItem(STORAGE_KEY, "1"); } catch { /* storage unavailable */ }
        setShow(false);
      }, wait + HOLD_MS + FADE_MS));
    });
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
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
        {lines.map((text, i) => {
          const isOk = text.startsWith("[OK]");
          const isFail = text.startsWith("[!!]");
          const isCmd = text.startsWith("$");
          return (
            <div
              key={i}
              style={{
                color: isCmd ? "var(--color-accent)" : isOk ? "var(--color-text)" : "var(--color-danger)",
                whiteSpace: "pre-wrap",
              }}
            >
              {isOk || isFail ? (
                <>
                  <span style={{ color: isOk ? "var(--color-accent)" : "var(--color-danger)" }}>
                    {text.slice(0, 4)}
                  </span>
                  <span>{text.slice(4)}</span>
                </>
              ) : (
                <span>{text}</span>
              )}
            </div>
          );
        })}
        {/* Blinking cursor under the last line */}
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
