import { useEffect, useRef } from "react";
import { useAgentState, type AgentKind } from "@/state/agentState";
import { ORBIT, SPHERE, TRAIL } from "./markGeometry";

/** How the orbiting point behaves in each state. It reads the same one word as the status pill, so the mark
 *  can never disagree with it: a slow lap on watch, a quicker one at work, parked at its resting place and
 *  pulsing when the agent needs you, and held still and dim when the last turn failed. */
const MOTION: Record<AgentKind, "watch" | "work" | "wait" | "still"> = {
  ready: "watch",
  done: "watch",
  stopped: "watch",
  thinking: "work",
  working: "work",
  waiting: "wait",
  stuck: "still",
};
const LAP = { watch: 18, work: 7 }; // seconds per lap
const TAU = Math.PI * 2;
const ROT = (ORBIT.rot * Math.PI) / 180;

/** A point on the orbit, an ellipse tilted around the sphere, in the mark's 64-unit grid. */
function at(t: number): [number, number] {
  const x = ORBIT.rx * Math.cos(t);
  const y = ORBIT.ry * Math.sin(t);
  return [ORBIT.cx + x * Math.cos(ROT) - y * Math.sin(ROT), ORBIT.cy + x * Math.sin(ROT) + y * Math.cos(ROT)];
}

/** How much of a point shows: on the far half of the orbit it fades where the sphere covers it. */
function seen(t: number): number {
  if (Math.sin(t) >= 0) return 1;
  const [x, y] = at(t);
  return Math.min(1, Math.max(0, (Math.hypot(x - ORBIT.cx, y - ORBIT.cy) - ORBIT.r + 1) / 4));
}

/** The app's mark: the presence's point sphere, and one point going round it with a short trail. */
export function Mark() {
  const { kind } = useAgentState();
  const end = useRef<SVGCircleElement>(null);
  const trail = useRef<(SVGCircleElement | null)[]>([]);
  // The frame loop reads the motion from here, so it never restarts when the state changes.
  const motion = useRef(MOTION[kind]);
  motion.current = MOTION[kind];

  useEffect(() => {
    const still = matchMedia("(prefers-reduced-motion: reduce)");
    let t = ORBIT.at;
    let held = 0;
    let last = performance.now();
    let raf = 0;
    const place = (tt: number, strength: number) => {
      const [x, y] = at(tt);
      end.current?.setAttribute("cx", x.toFixed(2));
      end.current?.setAttribute("cy", y.toFixed(2));
      end.current?.style.setProperty("opacity", String(strength * seen(tt)));
      // the point crosses the front of the sphere, so its trail follows on the near side and never rises off the top
      trail.current.forEach((c, k) => {
        const ti = tt + (k + 1) * TRAIL.step;
        const [tx, ty] = at(ti);
        c?.setAttribute("cx", tx.toFixed(2));
        c?.setAttribute("cy", ty.toFixed(2));
        c?.setAttribute("opacity", ((1 - (k + 1) / (TRAIL.count + 1)) * 0.75 * seen(ti) * strength).toFixed(2));
      });
    };
    const frame = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const m = motion.current;
      if (still.matches || m === "still") {
        held = 0;
        t = ORBIT.at;
        place(t, m === "still" ? 0.5 : 1);
      } else if (m === "wait") {
        // finish the lap to the resting place, then wait there and breathe
        const ahead = (((t - ORBIT.at) % TAU) + TAU) % TAU;
        if (ahead > 0.04 && !held) t -= Math.min(ahead, (TAU / 3) * dt);
        else {
          held += dt;
          t = ORBIT.at;
        }
        place(t, held ? 0.75 + 0.25 * Math.cos(held * 2.4) : 1);
      } else {
        held = 0;
        t = (((t - (TAU / LAP[m]) * dt) % TAU) + TAU) % TAU;
        place(t, 1);
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  const [ex, ey] = at(ORBIT.at);
  return (
    <svg className="mark" viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      {SPHERE.map(([cx, cy, r, tone], i) => (
        <circle key={i} cx={cx} cy={cy} r={r} className={`mark-${tone}`} />
      ))}
      {Array.from({ length: TRAIL.count }, (_, k) => (
        <circle
          key={`t${k}`}
          ref={(el) => {
            trail.current[k] = el;
          }}
          className="mark-trail"
          r={TRAIL.r * (1 - (0.45 * (k + 1)) / TRAIL.count)}
          cx={ex}
          cy={ey}
          opacity={0}
        />
      ))}
      <circle ref={end} className="mark-end" cx={ex} cy={ey} r={TRAIL.point} />
    </svg>
  );
}
