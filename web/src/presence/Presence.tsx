import { useEffect, useRef } from "react";
import { useAgentState, type AgentKind } from "@/state/agentState";
import { useTurnEvents } from "@/lib/turnEvents";
import { initCore, type CoreMode, type Pulse, type PulseTone } from "./coreGl";

const MODE: Record<AgentKind, CoreMode> = {
  ready: "idle",
  thinking: "think",
  working: "work",
  waiting: "wait",
  done: "idle",
  stuck: "stuck",
  stopped: "idle",
};
const TONE: Record<string, PulseTone> = { ok: "live", failed: "bad", waiting: "attn" };

const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Where the presence leans, from its centre toward an element: a unit vector with the
 *  screen's y flipped, tipped toward the viewer so the lit side stays in view. */
function toward(from: DOMRect, el: Element | null): [number, number, number] | null {
  if (!el) return null;
  const b = el.getBoundingClientRect();
  if (!b.width && !b.height) return null;
  const dx = b.left + Math.min(b.width * 0.22, 110) - (from.left + from.width / 2);
  const dy = b.top + b.height / 2 - (from.top + from.height / 2);
  const len = Math.hypot(dx, dy) || 1;
  const v: [number, number, number] = [dx / len, -dy / len, 0.5];
  const n = Math.hypot(...v);
  return [v[0] / n, v[1] / n, v[2] / n];
}

/** The agent's presence: the point sphere at the top of the conversation. It turns
 *  while the agent works, ripples once per tool step on the side facing the thread,
 *  faces what needs approval while it waits, flares green when a job is done, and sags
 *  when a turn ends in an error. Without WebGL the canvas stays empty and the status
 *  pill and spoken line carry the same state. */
export function Presence() {
  const canvas = useRef<HTMLCanvasElement>(null);
  const state = useAgentState();
  const { events } = useTurnEvents(1);
  // The frame loop reads these, so it never restarts when the state changes.
  const live = useRef({ mode: MODE[state.kind], kind: state.kind, pulses: [] as Pulse[], flareAt: -99 });
  live.current.mode = MODE[state.kind];
  live.current.kind = state.kind;

  useEffect(() => {
    if (state.kind === "done") live.current.flareAt = performance.now() / 1000;
  }, [state.kind]);

  useEffect(() => {
    const e = events[events.length - 1];
    const el = canvas.current;
    if (!e || e.event !== "tool" || !el || reduceMotion()) return;
    const at = toward(el.getBoundingClientRect(), document.querySelector(".thread")) ?? [0, 0, 1];
    const p = live.current.pulses;
    p.push({ o: at, t0: performance.now() / 1000, tone: TONE[e.outcome ?? "ok"] ?? "live" });
    if (p.length > 6) p.shift();
  }, [events]);

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const core = initCore(el);
    if (!core) return;
    const born = performance.now() / 1000;
    let last = performance.now();
    let raf = 0;
    const frame = (ms: number) => {
      const t = ms / 1000;
      const dt = Math.min(0.05, (ms - last) / 1000);
      last = ms;
      const reduce = reduceMotion();
      const r = el.getBoundingClientRect();
      const { mode, kind, pulses, flareAt } = live.current;
      const target = kind === "waiting" ? document.querySelector("#ask-panel") : kind === "working" ? document.querySelector(".thread") : null;
      const lean = reduce ? null : toward(r, target);
      if (r.width && r.height) {
        core.draw({
          w: r.width,
          h: r.height,
          dpr: Math.min(2, devicePixelRatio || 1),
          t: reduce ? 4 : t,
          dt: reduce ? 0 : dt,
          mode,
          pulses: pulses.filter((p) => t - p.t0 < 2.4),
          flare: reduce ? 0 : Math.max(0, 1 - (t - flareAt) / 1.6),
          amp: reduce ? 0.2 : 0,
          p: 0,
          born: reduce ? 1 : Math.min(1, (t - born) / 1.8),
          lean: lean ?? [0, 0, 1],
          leanAmt: lean ? (kind === "waiting" ? 1 : 0.7) : 0,
          reduce,
        });
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="core-wrap">
      <canvas className="core" ref={canvas} aria-hidden="true" />
    </div>
  );
}
