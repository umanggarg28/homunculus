import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { HomunculusRobot, type RobotState } from "@/components/robot/HomunculusRobot";

/**
 * ThinkingIndicator — the agent's pixel face INHABITS the loading state.
 *
 * Drop-in replacement for the previous dot+label indicator. Same public
 * API: `<ThinkingIndicator active={boolean} />`. Reads the live SSE feed
 * to (a) drive the robot's state (thinking ↔ working) and (b) stream a
 * compact tool-trace beside the face. When `active` flips false, it
 * collapses into a quiet "thought for Ns · k tools" summary line.
 *
 * Why: a generic spinner says "wait"; a living face mid-task says
 * "the agent is doing something for you." That difference is the brand.
 */

interface TraceStep {
  id: string;
  ts: number;
  kind: "llm" | "tool_call" | "tool_result" | "reply";
  label: string;
  isError?: boolean;
}

export function ThinkingIndicator({ active }: { active: boolean }) {
  const { events } = useEventStream(40);
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [robotState, setRobotState] = useState<RobotState>("thinking");
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef<number>(0);
  const seenRef = useRef<Set<string>>(new Set());
  const toolCountRef = useRef(0);

  // reset on each new "active" cycle
  useEffect(() => {
    if (active) {
      startRef.current = performance.now();
      seenRef.current = new Set();
      toolCountRef.current = 0;
      setSteps([]);
      setRobotState("thinking");
    }
  }, [active]);

  // elapsed timer
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setElapsed((performance.now() - startRef.current) / 1000), 100);
    return () => clearInterval(t);
  }, [active]);

  // ingest feed events into the trace + drive robot state
  useEffect(() => {
    if (!active) return;
    const next: TraceStep[] = [];
    for (const e of events) {
      const id = `${e.ts}-${e.event}-${e.name ?? ""}`;
      if (seenRef.current.has(id)) continue;
      const ts = new Date(e.ts).getTime();
      if (e.event === "llm_call") {
        next.push({ id, ts, kind: "llm", label: "llm_call · reasoning" });
      } else if (e.event === "tool_call" && e.name) {
        toolCountRef.current += 1;
        next.push({ id, ts, kind: "tool_call", label: `${e.name}(…)` });
      } else if (e.event === "tool_result") {
        const isError = typeof e.result === "string" && e.result.startsWith("ERROR");
        next.push({ id, ts, kind: "tool_result", label: isError ? "error" : "ok", isError });
      }
      seenRef.current.add(id);
    }
    if (next.length) {
      setSteps((prev) => [...prev, ...next].slice(-5));
      const last = next[next.length - 1];
      setRobotState(last.kind === "tool_call" ? "working" : "thinking");
    }
  }, [events, active]);

  // ── collapsed summary (after the turn) ───────────────────────────
  if (!active) {
    if (steps.length === 0) return null;
    return (
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        className="flex items-center gap-2 mt-2 text-[11px] uppercase tracking-[0.14em]"
        style={{ color: "var(--color-text-muted)" }}
      >
        ▸ thought for {elapsed.toFixed(1)}s · {toolCountRef.current} tool{toolCountRef.current === 1 ? "" : "s"}
      </motion.div>
    );
  }

  // ── active indicator: face + live trace ──────────────────────────
  return (
    <motion.div
      initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
      className="grid mt-3 gap-3"
      style={{ gridTemplateColumns: "40px 1fr" }}
    >
      {/* the face IS the spinner */}
      <div
        style={{
          width: 38, height: 44,
          border: "1px solid var(--color-border)",
          background: "var(--color-surface-1)",
        }}
      >
        <HomunculusRobot state={robotState} detail="low" style={{ width: "100%", height: "100%" }} />
      </div>

      <div className="flex flex-col gap-2 pt-0.5 min-w-0">
        <div className="flex items-center gap-2.5">
          <span className="text-[11px] uppercase tracking-[0.14em]" style={{ color: "var(--color-accent)" }}>
            {robotState === "working" ? "Working" : "Thinking"}
          </span>
          <ThinkDots />
          <span className="ml-auto text-[10px] tracking-[0.12em]" style={{ color: "var(--color-text-muted)" }}>
            {elapsed.toFixed(1)}s
          </span>
        </div>

        <div
          className="flex flex-col gap-1 pl-3 min-w-0"
          style={{ borderLeft: "1px solid var(--color-border-strong)" }}
        >
          <AnimatePresence initial={false}>
            {steps.map((s, i) => (
              <motion.div
                key={s.id}
                initial={{ opacity: 0, x: -4 }}
                animate={{ opacity: i === steps.length - 1 ? 1 : 0.5, x: 0 }}
                exit={{ opacity: 0 }}
                className="text-[11.5px] truncate"
                style={{ color: "var(--color-text-muted)" }}
              >
                <span style={{ color: "var(--color-text-faint)", marginRight: 8 }}>{fmtTime(s.ts)}</span>
                <span style={{ color: glyphColor(s), marginRight: 6 }}>{glyph(s)}</span>
                <span style={{ color: "var(--color-text-dim)" }}>{s.label}</span>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </div>
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
  switch (s.kind) {
    case "llm": return "λ";
    case "tool_call": return "→";
    case "tool_result": return s.isError ? "✗" : "←";
    case "reply": return "›";
  }
}
function glyphColor(s: TraceStep): string {
  if (s.isError) return "var(--color-danger)";
  if (s.kind === "llm") return "var(--color-indigo)";
  return "var(--color-accent)";
}
function fmtTime(ms: number): string {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function pad(n: number): string { return n.toString().padStart(2, "0"); }
