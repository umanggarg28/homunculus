import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { isFailedToolEvent, useTurnEvents } from "@/lib/turnEvents";

interface TraceStep {
  id: string;
  ts: number;
  label: string;
  isError?: boolean;
}

export function ThinkingIndicator({ active }: { active: boolean }) {
  const { events } = useTurnEvents(40);
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [phase, setPhase] = useState<"thinking" | "working">("thinking");
  const [elapsed, setElapsed] = useState(0);
  const startPerfRef = useRef<number>(0);
  const startWallRef = useRef<number>(0);
  const seenRef = useRef<Set<string>>(new Set());
  const toolCountRef = useRef(0);

  // Reset on each new turn.
  useEffect(() => {
    if (active) {
      startPerfRef.current = performance.now();
      startWallRef.current = Date.now();
      seenRef.current = new Set();
      toolCountRef.current = 0;
      setSteps([]);
      setPhase("thinking");
      setElapsed(0);
    }
  }, [active]);

  // Elapsed timer.
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setElapsed((performance.now() - startPerfRef.current) / 1000), 100);
    return () => clearInterval(t);
  }, [active]);

  // Take in this turn's tool steps as they arrive.
  useEffect(() => {
    if (!active) return;
    const next: TraceStep[] = [];
    for (const e of events) {
      const ts = new Date(e.ts).getTime();
      // Skip events from before this turn started.
      if (ts < startWallRef.current - 2000) continue;
      const id = `${e.ts}-${e.event}-${e.name ?? ""}`;
      if (seenRef.current.has(id)) continue;
      seenRef.current.add(id);
      if (e.event === "tool" && e.name) {
        toolCountRef.current += 1;
        const isError = isFailedToolEvent(e);
        const label = isError ? `${e.name}: ${e.answer === "act" ? "error" : e.answer}` : e.name;
        next.push({ id, ts, label, isError });
      }
    }
    if (next.length) {
      setSteps((prev) => [...prev, ...next].slice(-5));
      setPhase("working");
    }
  }, [events, active]);

  // Collapsed summary shown after the turn completes.
  if (!active) {
    if (steps.length === 0) return null;
    const n = toolCountRef.current;
    return (
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        className="flex items-center gap-2 mt-2 text-[11px] uppercase tracking-[0.14em]"
        style={{ color: "var(--color-text-muted)" }}
      >
        ▸ thought for {elapsed.toFixed(1)}s{n > 0 ? ` · ${n} tool${n === 1 ? "" : "s"}` : ""}
      </motion.div>
    );
  }

  // Active indicator: status text + live trace (no duplicate robot: sidebar has one).
  return (
    <motion.div
      initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
      className="flex flex-col gap-2 mt-3 min-w-0"
    >
      <div className="flex items-center gap-2.5">
        <span className="text-[11px] uppercase tracking-[0.14em]" style={{ color: "var(--color-accent)" }}>
          {phase === "working" ? "Working" : "Thinking"}
        </span>
        <ThinkDots />
        <span className="ml-auto text-[10px] tracking-[0.12em]" style={{ color: "var(--color-text-muted)" }}>
          {elapsed.toFixed(1)}s
        </span>
      </div>

      {steps.length > 0 && (
        <div
          className="flex flex-col gap-1 pl-3 min-w-0"
          style={{ borderLeft: "1px solid var(--color-border-strong)" }}
        >
          <AnimatePresence initial={false}>
            {steps.map((s, i) => (
              <motion.div
                key={s.id}
                initial={{ opacity: 0, x: -4 }}
                animate={{ opacity: i === steps.length - 1 ? 1 : 0.45, x: 0 }}
                exit={{ opacity: 0 }}
                className="text-[11.5px] truncate"
              >
                <span style={{ color: "var(--color-text-faint)", marginRight: 8 }}>{fmtTime(s.ts)}</span>
                <span style={{ color: glyphColor(s), marginRight: 6 }}>{glyph(s)}</span>
                <span style={{ color: "var(--color-text-dim)" }}>{s.label}</span>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </motion.div>
  );
}

function ThinkDots() {
  return (
    <span className="inline-flex gap-[3px]">
      {[0, 1, 2].map((i) => (
        <motion.i
          key={i}
          className="inline-block"
          style={{ width: 4, height: 4, background: "var(--color-accent)", boxShadow: "0 0 5px var(--color-accent-glow)" }}
          animate={{ opacity: [0.25, 1, 0.25] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2, ease: "easeInOut" }}
        />
      ))}
    </span>
  );
}

function glyph(s: TraceStep): string {
  return s.isError ? "✗" : "←";
}
function glyphColor(s: TraceStep): string {
  return s.isError ? "var(--color-danger)" : "var(--color-accent)";
}
function fmtTime(ms: number): string {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function pad(n: number): string { return n.toString().padStart(2, "0"); }
