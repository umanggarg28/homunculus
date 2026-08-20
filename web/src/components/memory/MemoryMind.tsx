import { useEffect, useMemo, useRef, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import type { MemoryEntry } from "@/lib/types";

/**
 * MemoryMind — the vault as a temporal organ.
 *
 * AGE IS RADIUS. A note written an hour ago sits tight against the glowing
 * core; one from nine weeks ago drifts dim toward the rim, so decay is
 * something you look at rather than compute. Dashed bands label
 * now / this week / this month / older.
 *
 * The signature moment is recall: any feed event naming a memory ignites
 * that node and a pulse travels its links two hops out. It is wired to the
 * real event stream, so what you see is the agent actually reading — the
 * "trace a recall" button is a demo affordance, not the data source.
 *
 * Layout is seeded from the vault's own contents, so the map does not
 * reshuffle between loads; level-of-detail keeps a large vault readable.
 */

interface Props {
  entries: MemoryEntry[];
  /** panel height in px */
  height?: number;
}

const DAY = 86400;

const TYPE_COLOR: Record<string, string> = {
  user: "108,231,255",       // --color-indigo
  feedback: "255,184,77",    // --color-amber
  project: "215,245,223",    // --color-text
  reference: "147,199,167",  // --color-text-dim
  skill: "119,255,61",       // --color-accent
};
const ACCENT = "119,255,61";
const ACCENT_HOT = "200,255,150";
const EDGE_COLD = "40,84,67";

interface Node {
  i: number;
  e: MemoryEntry;
  ang: number;
  radius: number;
  x: number;
  y: number;
  ageN: number;
  orbit: number;
  breathe: number;
  heat: number;
  recalls: number;
  born: number;
}
interface Pulse { a: number; b: number; t: number; dur: number }

function seeded(s: number) {
  let a = s >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Canonical form of a memory reference. An entry is reachable by its `name`
 * OR its filename stem, and the vault uses both `-` and `_` — `name:
 * daily_log_summary` lives in `feedback_daily_log_summary.md`, so matching a
 * link on `name` alone drops edges that are really there.
 */
const slug = (v: string) => v.trim().toLowerCase().replace(/-/g, "_");

/** stem a memory name for display */
function shortName(nm: string) {
  const s = nm.replace(/^(project|feedback|reference|user|skill)[-_]/, "");
  // Dropping the prefix off `user_name` leaves "name", which labels the node
  // with a word that describes every node. Keep the full slug when the stem
  // is too short or too generic to identify anything.
  const useful = s.length >= 5 && !["name", "role", "notes", "state", "log"].includes(s);
  const out = useful ? s : nm;
  return out.length > 20 ? out.slice(0, 20) + "…" : out;
}
function fmtAge(mtime: number, now: number) {
  const d = (now - mtime) / DAY;
  if (d < 1) return `${Math.max(1, Math.round(d * 24))}h ago`;
  if (d < 7) return `${Math.round(d)}d ago`;
  return `${Math.round(d / 7)}w ago`;
}

export function MemoryMind({ entries, height = 560 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const { events } = useEventStream(40);

  const [focus, setFocus] = useState(-1);
  const [recallCount, setRecallCount] = useState(0);
  const [banner, setBanner] = useState<{ text: string; state: string } | null>(null);

  // ── build the graph once per entries change ─────────────────────
  const graph = useMemo(() => {
    const now = Math.floor(Date.now() / 1000);
    // An entry answers to its name AND its filename stem (see `slug`).
    const byKey = new Map<string, number>();
    entries.forEach((e, i) => {
      byKey.set(slug(e.name), i);
      byKey.set(slug(e.filename.replace(/\.md$/i, "")), i);
    });
    const edges: [number, number][] = [];
    const seen = new Set<string>();
    entries.forEach((e, i) => (e.links ?? []).forEach((s) => {
      const j = byKey.get(slug(s));
      if (j === undefined || j === i) return;
      const k = `${Math.min(i, j)}|${Math.max(i, j)}`;
      if (seen.has(k)) return;
      seen.add(k);
      edges.push([Math.min(i, j), Math.max(i, j)]);
    }));
    const degree = entries.map(() => 0);
    edges.forEach(([a, b]) => { degree[a]++; degree[b]++; });

    // deterministic seed from the vault contents
    let sd = entries.length * 7919;
    for (const e of entries) for (let k = 0; k < e.name.length; k++) sd = (sd * 31 + e.name.charCodeAt(k)) | 0;
    const rnd = seeded(sd);

    const ages = entries.map((e) => (now - e.mtime) / DAY);
    const maxAge = Math.max(...ages, 1);
    const N = entries.length;

    const nodes: Node[] = entries.map((e, i) => {
      const ageN = Math.pow(ages[i] / maxAge, 0.62);
      const ang = rnd() * Math.PI * 2;
      const radius = 95 + ageN * 325;
      return {
        i, e, ang, radius,
        x: Math.cos(ang) * radius, y: Math.sin(ang) * radius * 0.72,
        ageN,
        orbit: (rnd() - 0.5) * 0.00042,
        breathe: rnd() * Math.PI * 2,
        heat: 0,
        recalls: 0,
        born: 0,
      };
    });

    // angular relaxation — radius stays pinned to age
    const iters = N > 120 ? 140 : 300;
    for (let it = 0; it < iters; it++) {
      const t = 1 - it / iters;
      const fa = nodes.map(() => 0);
      for (let a = 0; a < N; a++) for (let b = a + 1; b < N; b++) {
        let d = nodes[a].ang - nodes[b].ang;
        while (d > Math.PI) d -= Math.PI * 2;
        while (d < -Math.PI) d += Math.PI * 2;
        const rd = Math.abs(nodes[a].radius - nodes[b].radius);
        const push = 0.9 / (Math.abs(d) + 0.12) / (1 + rd / 120);
        fa[a] += Math.sign(d || 1) * push;
        fa[b] -= Math.sign(d || 1) * push;
      }
      edges.forEach(([a, b]) => {
        let d = nodes[b].ang - nodes[a].ang;
        while (d > Math.PI) d -= Math.PI * 2;
        while (d < -Math.PI) d += Math.PI * 2;
        fa[a] += d * 0.22; fa[b] -= d * 0.22;
      });
      nodes.forEach((n, i) => { n.ang += Math.max(-0.09, Math.min(0.09, fa[i] * 0.02)) * t; });
    }
    nodes.forEach((n) => { n.x = Math.cos(n.ang) * n.radius; n.y = Math.sin(n.ang) * n.radius * 0.72; });

    return { nodes, edges, degree, byKey, now };
  }, [entries]);

  // mutable render state kept out of React
  const stateRef = useRef({
    cam: { x: 0, y: 0, z: 1, tx: 0, ty: 0, tz: 1 },
    hover: -1,
    focus: -1,
    pulses: [] as Pulse[],
    drag: false, dragged: false, lx: 0, ly: 0,
    T: 0,
  });
  useEffect(() => { stateRef.current.focus = focus; }, [focus]);

  const bannerTimer = useRef<number | null>(null);
  // Timers spawned by a recall cascade, so unmount cannot leave them firing
  // into a disposed graph.
  const cascadeTimers = useRef<number[]>([]);
  useEffect(() => () => {
    if (bannerTimer.current) window.clearTimeout(bannerTimer.current);
    cascadeTimers.current.forEach((t) => window.clearTimeout(t));
    cascadeTimers.current = [];
  }, []);

  const flash = (text: string, state: string) => {
    setBanner({ text, state });
    if (bannerTimer.current) window.clearTimeout(bannerTimer.current);
    bannerTimer.current = window.setTimeout(() => setBanner(null), 2300);
  };

  // ── recall: ignite + propagate along links ──────────────────────
  const recallRef = useRef<(i: number, depth?: number, seen?: Set<number>) => void>(() => {});
  recallRef.current = (i: number, depth = 0, seen = new Set<number>()) => {
    const { nodes, edges } = graph;
    if (i < 0 || !nodes[i]) return;
    seen.add(i);
    nodes[i].heat = 1;
    if (depth === 0) {
      nodes[i].recalls++;
      setRecallCount((c) => c + 1);
      flash(nodes[i].e.name, "RECALLING");
    }
    if (depth >= 2) return;
    edges.forEach(([a, b]) => {
      const other = a === i ? b : b === i ? a : -1;
      if (other < 0 || seen.has(other)) return;
      stateRef.current.pulses.push({ a: i, b: other, t: 0, dur: 0.72 + Math.random() * 0.3 });
      const t = window.setTimeout(
        () => recallRef.current(other, depth + 1, seen),
        620 + Math.random() * 260,
      );
      cascadeTimers.current.push(t);
    });
  };

  // ── wire recall to the live feed ────────────────────────────────
  // Only events that actually touch memory can ignite a node. Matching every
  // event against every name lights the map up on unrelated traffic — a task
  // whose title happens to contain a memory's name would look like a recall.
  const MEMORY_EVENTS = useMemo(
    () => new Set(["tool_call", "tool_result", "memory_write", "memory_read"]),
    [],
  );
  const seenEvents = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const ev of events) {
      const id = `${ev.ts}-${ev.event}-${ev.name ?? ""}-${(ev.result ?? "").slice(0, 24)}`;
      if (seenEvents.current.has(id)) continue;
      seenEvents.current.add(id);
      if (!MEMORY_EVENTS.has(ev.event)) continue;
      const hay = slug(`${ev.name ?? ""} ${typeof ev.result === "string" ? ev.result : ""}`);
      if (!hay.trim()) continue;
      for (const [key, idx] of graph.byKey) {
        // Guard against a 2-3 char name matching half the feed.
        if (key.length >= 6 && hay.includes(key)) { recallRef.current(idx); break; }
      }
    }
  }, [events, graph, MEMORY_EVENTS]);

  // ── render loop ────────────────────────────────────────────────
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const g = cv.getContext("2d");
    if (!g) return;
    const { nodes, edges, degree } = graph;
    const st = stateRef.current;
    let raf = 0;
    let VW = 0, VH = 0, DPR = 1;

    const fit = () => {
      const r = cv.getBoundingClientRect();
      DPR = Math.min(2, window.devicePixelRatio || 1);
      cv.width = Math.max(2, Math.floor(r.width * DPR));
      cv.height = Math.max(2, Math.floor(r.height * DPR));
      VW = cv.width; VH = cv.height;
    };
    // The layout is built in fixed world units, but the panel is far wider
    // than it is tall. At zoom 1 the outermost band falls off the top and
    // bottom, so the "older" ring — the whole point of the age axis — was
    // never visible. Derive a base zoom that fits the full extent, and treat
    // the user's zoom as a multiplier on top of it.
    const WORLD_R = 440;          // outermost band + a little margin
    const SQUASH = 0.72;          // the ellipse flattening used by the layout
    let baseZoom = 1;
    const fitZoom = () => {
      if (!VW || !VH) return;
      baseZoom = Math.min(
        VW / (2 * WORLD_R * DPR),
        VH / (2 * WORLD_R * SQUASH * DPR),
      ) * 0.94;                   // breathing room inside the panel edge
    };
    const measure = () => { fit(); fitZoom(); };
    measure();
    const ro = new ResizeObserver(measure);
    if (wrapRef.current) ro.observe(wrapRef.current);

    /** User zoom folded with the fit-to-panel zoom. Every screen-space
     *  calculation must use this, never cam.z alone, or hit-testing drifts
     *  away from what is drawn. */
    const Z = () => st.cam.z * baseZoom;

    const toScreen = (wx: number, wy: number): [number, number] =>
      [VW / 2 + (wx - st.cam.x) * Z() * DPR, VH / 2 + (wy - st.cam.y) * Z() * DPR];

    const EDGE_CAP = 900;
    const MONO = '"JetBrains Mono", ui-monospace, monospace';

    const frame = () => {
      st.T += 0.016;
      const cam = st.cam;
      cam.x += (cam.tx - cam.x) * 0.075;
      cam.y += (cam.ty - cam.y) * 0.075;
      cam.z += (cam.tz - cam.z) * 0.09;

      g.fillStyle = "#060A08";
      g.fillRect(0, 0, VW, VH);

      const active = st.focus >= 0 ? st.focus : st.hover;
      const neighbors = new Set<number>();
      if (active >= 0) {
        neighbors.add(active);
        edges.forEach(([a, b]) => { if (a === active) neighbors.add(b); if (b === active) neighbors.add(a); });
      }

      // time bands — radius IS age
      const [ccx, ccy] = toScreen(0, 0);
      ([[95, "now"], [200, "this week"], [320, "this month"], [425, "older"]] as [number, string][])
        .forEach(([r, label]) => {
          g.save(); g.translate(ccx, ccy); g.scale(1, 0.72);
          g.beginPath(); g.arc(0, 0, r * Z() * DPR, 0, Math.PI * 2);
          g.strokeStyle = "rgba(30,66,51,.95)"; g.lineWidth = DPR;
          g.setLineDash([2 * DPR, 5 * DPR]); g.stroke();
          g.restore(); g.setLineDash([]);
          const [lx, ly] = toScreen(0, -r * SQUASH);
          const cap = label.toUpperCase();
          g.font = `${8 * DPR}px ${MONO}`;
          g.textAlign = "center";
          // Knock the ring out behind the caption — the band line and a node
          // label crossing the same pixels made both unreadable.
          const w = g.measureText(cap).width;
          g.fillStyle = "#060A08";
          g.fillRect(lx - w / 2 - 4 * DPR, ly - 13 * DPR, w + 8 * DPR, 11 * DPR);
          g.fillStyle = "rgba(88,140,113,.95)";
          g.fillText(cap, lx, ly - 5 * DPR);
        });

      // the core — the agent's attention
      const cp = 0.5 + 0.5 * Math.sin(st.T * 1.5);
      const gr = g.createRadialGradient(ccx, ccy, 0, ccx, ccy, 88 * Z() * DPR);
      gr.addColorStop(0, `rgba(${ACCENT},${0.16 + cp * 0.09})`);
      gr.addColorStop(0.45, `rgba(${ACCENT},.05)`);
      gr.addColorStop(1, `rgba(${ACCENT},0)`);
      g.fillStyle = gr;
      g.beginPath(); g.arc(ccx, ccy, 88 * Z() * DPR, 0, Math.PI * 2); g.fill();
      g.fillStyle = `rgba(${ACCENT_HOT},${0.7 + cp * 0.3})`;
      const cs = 3.4 * Z() * DPR;
      g.fillRect(ccx - cs / 2, ccy - cs / 2, cs, cs);
      g.fillStyle = "rgba(72,119,96,.85)";
      g.font = `${7.5 * DPR}px ${MONO}`;
      g.textAlign = "center";
      g.fillText("CORE", ccx, ccy + 19 * DPR);

      // drift + cool
      nodes.forEach((n) => {
        n.ang += n.orbit;
        if (n.born > 0) n.born = Math.max(0, n.born - 0.02);
        const r = n.radius + Math.sin(st.T * 0.55 + n.breathe) * 3.4;
        n.x = Math.cos(n.ang) * r;
        n.y = Math.sin(n.ang) * r * 0.72;
        n.heat = Math.max(0, n.heat - 0.0085);
      });

      // edges (LOD: when zoomed out on a big vault, only lit/hot edges)
      const sparse = edges.length > EDGE_CAP && Z() < 1.1;
      edges.forEach(([a, b]) => {
        const A = nodes[a], B = nodes[b];
        if (!A || !B) return;
        const lit = active >= 0 && (a === active || b === active);
        const heat = Math.max(A.heat, B.heat);
        if (sparse && !lit && heat < 0.1) return;
        const [x1, y1] = toScreen(A.x, A.y);
        const [x2, y2] = toScreen(B.x, B.y);
        let op = active === -1 ? 0.46 : lit ? 0.95 : 0.1;
        op = Math.min(1, op + heat * 0.5);
        g.strokeStyle = (lit || heat > 0.1) ? `rgba(${ACCENT},${op})` : `rgba(${EDGE_COLD},${op})`;
        g.lineWidth = (lit ? 1.3 : 1) * DPR;
        g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
      });

      // recall pulses travelling the links
      for (let i = st.pulses.length - 1; i >= 0; i--) {
        const p = st.pulses[i];
        p.t += 0.016 / p.dur;
        const A = nodes[p.a], B = nodes[p.b];
        if (p.t >= 1 || !A || !B) { st.pulses.splice(i, 1); continue; }
        const e = p.t < 0.5 ? 2 * p.t * p.t : 1 - Math.pow(-2 * p.t + 2, 2) / 2;
        const [px, py] = toScreen(A.x + (B.x - A.x) * e, A.y + (B.y - A.y) * e);
        const fade = Math.sin(p.t * Math.PI);
        g.shadowColor = `rgb(${ACCENT})`; g.shadowBlur = 11 * DPR;
        g.fillStyle = `rgba(${ACCENT_HOT},${fade})`;
        const s = 3.2 * Z() * DPR;
        g.fillRect(px - s / 2, py - s / 2, s, s);
        g.shadowBlur = 0;
      }

      // nodes
      const labelBudget = nodes.length > 80 ? 3 : 2;
      nodes.forEach((n) => {
        const [x, y] = toScreen(n.x, n.y);
        if (x < -60 || x > VW + 60 || y < -60 || y > VH + 60) return;
        const dimmed = active >= 0 && !neighbors.has(n.i);
        const rec = 1 - n.ageN;
        const base = TYPE_COLOR[n.e.type] ?? "147,199,167";
        let s = (3.4 + Math.min(degree[n.i], 6) * 0.62 + rec * 1.5) * Z() * DPR;
        if (n.born > 0) s *= 1 + n.born * 2.2;
        // A cold entry must still read as a point of light. The original
        // floor was tuned for a full-screen black page; inside a panel at
        // this size the oldest two thirds of the vault disappeared and the
        // map looked empty.
        let op = 0.62 + rec * 0.34;
        if (dimmed) op *= 0.28;
        op = Math.min(1, op + n.heat * 0.7);
        const isHot = n.heat > 0.08 || n.i === active;
        g.fillStyle = isHot ? `rgba(${ACCENT_HOT},${Math.min(1, op + 0.25)})` : `rgba(${base},${op})`;
        if (isHot || rec > 0.72) { g.shadowColor = `rgb(${ACCENT})`; g.shadowBlur = (6 + n.heat * 16) * DPR; }
        else g.shadowBlur = 0;
        g.fillRect(x - s / 2, y - s / 2, s, s);
        g.shadowBlur = 0;
        if (n.heat > 0.02) {
          g.strokeStyle = `rgba(${ACCENT},${n.heat * 0.55})`;
          g.lineWidth = DPR;
          g.beginPath(); g.arc(x, y, (9 + (1 - n.heat) * 26) * Z() * DPR, 0, Math.PI * 2); g.stroke();
        }
        if ((degree[n.i] >= labelBudget && !dimmed && Z() > 0.55) || n.i === active || n.heat > 0.25) {
          const label = shortName(n.e.name);
          g.font = `${8.4 * DPR}px ${MONO}`;
          g.textAlign = "center";
          const lw = g.measureText(label).width;
          g.fillStyle = "rgba(6,10,8,.82)";
          g.fillRect(x - lw / 2 - 3 * DPR, y + s / 2 + 3 * DPR, lw + 6 * DPR, 11 * DPR);
          g.fillStyle = (n.i === active || n.heat > 0.25) ? "rgba(140,253,97,.98)" : "rgba(104,158,128,.95)";
          g.fillText(label, x, y + s / 2 + 11 * DPR);
        }
      });

      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    // ── interaction ──
    const toWorld = (sx: number, sy: number): [number, number] => {
      const r = cv.getBoundingClientRect();
      const px = (sx - r.left) * DPR, py = (sy - r.top) * DPR;
      return [(px - VW / 2) / (Z() * DPR) + st.cam.x, (py - VH / 2) / (Z() * DPR) + st.cam.y];
    };
    const onDown = (e: MouseEvent) => { st.drag = true; st.dragged = false; st.lx = e.clientX; st.ly = e.clientY; };
    const onUp = () => { st.drag = false; };
    /** The node under a viewport coordinate, or -1. */
    const pick = (clientX: number, clientY: number): number => {
      const [wx, wy] = toWorld(clientX, clientY);
      let best = -1, bd = Infinity;
      nodes.forEach((n) => {
        const d = Math.hypot(n.x - wx, n.y - wy);
        if (d < bd && d < 26 / Z()) { bd = d; best = n.i; }
      });
      return best;
    };

    const onMove = (e: MouseEvent) => {
      if (st.drag) {
        const dx = e.clientX - st.lx, dy = e.clientY - st.ly;
        if (Math.abs(dx) + Math.abs(dy) > 3) st.dragged = true;
        st.cam.tx -= dx / Z(); st.cam.ty -= dy / Z();
        st.cam.x -= dx / Z(); st.cam.y -= dy / Z();
        st.lx = e.clientX; st.ly = e.clientY;
        return;
      }
      st.hover = pick(e.clientX, e.clientY);
      cv.style.cursor = st.hover >= 0 ? "pointer" : "crosshair";
    };

    const onClick = (e: MouseEvent) => {
      if (st.dragged) return;
      // Hit-test the click's OWN coordinates rather than trusting the hover
      // the last mousemove left behind. A touch device never produces that
      // move, so tapping a node did nothing at all on tablets and phones.
      const i = pick(e.clientX, e.clientY);
      if (i >= 0) {
        setFocus(i);
        st.cam.tx = nodes[i].x; st.cam.ty = nodes[i].y; st.cam.tz = 1.5;
      } else {
        setFocus(-1);
        st.cam.tx = 0; st.cam.ty = 0; st.cam.tz = 1;
      }
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      st.cam.tz = Math.max(0.42, Math.min(2.6, st.cam.tz * Math.exp(-e.deltaY * 0.0012)));
    };

    cv.addEventListener("mousedown", onDown);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("mousemove", onMove);
    cv.addEventListener("click", onClick);
    cv.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      cv.removeEventListener("mousedown", onDown);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("mousemove", onMove);
      cv.removeEventListener("click", onClick);
      cv.removeEventListener("wheel", onWheel);
    };
  }, [graph]);

  const focused = focus >= 0 ? graph.nodes[focus] : null;
  const focusedNeighbors = useMemo(() => {
    if (focus < 0) return [];
    const out: number[] = [];
    graph.edges.forEach(([a, b]) => { if (a === focus) out.push(b); if (b === focus) out.push(a); });
    return [...new Set(out)];
  }, [focus, graph]);

  if (entries.length === 0) return null;

  return (
    <div
      ref={wrapRef}
      className="instrument-panel mb-4 relative overflow-hidden"
      style={{ height, fontFamily: "var(--font-mono)" }}
    >
      <canvas ref={canvasRef} className="absolute inset-0 w-full h-full block" />

      {/* header */}
      <div className="absolute top-0 left-0 right-0 flex items-start gap-6 px-4 pt-3 pointer-events-none">
        <div>
          <div className="brut-label" style={{ color: "var(--color-text)" }}>the mind</div>
          <div className="brut-meta mt-1" style={{ color: "var(--color-text-muted)" }}>
            age is radius · recall travels the links
          </div>
        </div>
        <div className="ml-auto flex gap-5 items-baseline">
          <Stat k="state" v={banner?.state ?? "RESTING"} hot={!!banner} />
          <Stat k="entries" v={String(entries.length)} />
          <Stat k="links" v={String(graph.edges.length)} />
          <Stat k="recalls" v={String(recallCount)} />
        </div>
      </div>

      {/* recall banner */}
      {banner && (
        <div
          className="absolute left-1/2 top-3 -translate-x-1/2 px-4 py-2 whitespace-nowrap pointer-events-none"
          style={{
            border: "1px solid var(--color-border-bright)",
            background: "rgba(6,10,8,.92)",
            fontSize: 10, letterSpacing: ".16em", textTransform: "uppercase",
            color: "var(--color-accent)",
            boxShadow: "0 0 34px -10px var(--color-accent-glow)",
          }}
        >
          ◎ recalled · <b style={{ color: "var(--color-text)", fontWeight: 500 }}>{banner.text}</b>
        </div>
      )}

      {/* detail card */}
      {focused && (
        <div
          className="absolute right-4 top-1/2 -translate-y-1/2 w-[300px]"
          style={{
            border: "1px solid var(--color-border-strong)",
            background: "linear-gradient(180deg,rgba(119,255,61,.03),transparent), var(--color-surface-1)",
          }}
        >
          <button
            onClick={() => { setFocus(-1); const c = stateRef.current.cam; c.tx = 0; c.ty = 0; c.tz = 1; }}
            className="absolute top-2 right-2"
            aria-label="Close entry detail"
            style={{ background: "none", border: "none", color: "var(--color-text-muted)", cursor: "pointer", fontSize: 11 }}
          >✕</button>
          <div className="px-3.5 py-3 flex justify-between items-baseline gap-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
            <span style={{ fontSize: 11, letterSpacing: ".1em", color: "var(--color-accent)", fontWeight: 600, overflowWrap: "anywhere" }}>
              {focused.e.name}
            </span>
            <span className="brut-meta whitespace-nowrap" style={{ color: "var(--color-text-muted)" }}>{focused.e.type}</span>
          </div>
          <div className="px-3.5 py-3" style={{ fontSize: 11.5, lineHeight: 1.6, color: "var(--color-text-dim)" }}>
            {focused.e.description}
          </div>
          <div className="px-3.5 py-2.5 flex gap-4 flex-wrap" style={{ borderTop: "1px solid var(--color-border)" }}>
            <Meta k="written" v={fmtAge(focused.e.mtime, graph.now)} />
            <Meta k="links" v={String(graph.degree[focus])} />
            <Meta k="recalls" v={String(focused.recalls)} />
          </div>
          {focusedNeighbors.length > 0 && (
            <div className="px-3.5 py-2.5" style={{ borderTop: "1px solid var(--color-border)" }}>
              <div className="brut-meta mb-1.5" style={{ color: "var(--color-text-faint)" }}>── linked to</div>
              {focusedNeighbors.map((j) => (
                <button
                  key={j}
                  onClick={() => {
                    setFocus(j);
                    const c = stateRef.current.cam;
                    c.tx = graph.nodes[j].x; c.ty = graph.nodes[j].y; c.tz = 1.5;
                  }}
                  className="block w-full text-left py-0.5"
                  style={{ background: "none", border: "none", cursor: "pointer", fontSize: 10.5, color: "var(--color-text-muted)" }}
                >
                  ↳ {graph.nodes[j].e.name}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* controls */}
      <div className="absolute bottom-3 right-4 flex gap-1.5 items-center">
        <span className="brut-meta mr-3 whitespace-nowrap hidden xl:inline" style={{ color: "var(--color-text-faint)" }}>
          drag to pan · scroll to zoom
        </span>
        <button
          onClick={() => {
            const pool = graph.nodes.slice().sort((a, b) => a.ageN - b.ageN).slice(0, 10);
            if (pool.length) recallRef.current(pool[Math.floor(Math.random() * pool.length)].i);
          }}
          className="brut-label"
          style={{ border: "1px solid var(--color-border)", background: "transparent", color: "var(--color-text-muted)", padding: "7px 10px", cursor: "pointer" }}
        >◎ trace a recall</button>
      </div>
    </div>
  );
}

function Stat({ k, v, hot }: { k: string; v: string; hot?: boolean }) {
  return (
    <span className="brut-meta whitespace-nowrap" style={{ color: "var(--color-text-faint)" }}>
      {k}
      <b style={{
        color: hot ? "var(--color-accent)" : "var(--color-text-dim)",
        fontWeight: 500, marginLeft: 6, fontVariantNumeric: "tabular-nums",
        textShadow: hot ? "0 0 9px var(--color-accent-glow)" : "none",
      }}>{v}</b>
    </span>
  );
}
function Meta({ k, v }: { k: string; v: string }) {
  return (
    <span className="brut-meta" style={{ color: "var(--color-text-faint)" }}>
      {k}<b style={{ color: "var(--color-text-dim)", fontWeight: 500, marginLeft: 5 }}>{v}</b>
    </span>
  );
}
