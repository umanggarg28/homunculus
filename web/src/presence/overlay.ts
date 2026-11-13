/** Work travels from the presence to where it lands, as a trace of the same square points
 *  the presence is made of. Ported from the design mock.
 *
 *  Two canvases cover the screen and ignore the pointer. The bus is drawn faintly in the
 *  gaps between panels, with the presence as its hub. A trace writes itself along a
 *  right-angled route through those gaps, lands with brackets closing on its target, then
 *  erases from the tail. A refusal is a trace that stops at a short wall. Nothing glows.
 *
 *  Below 600px, and for people who ask for less motion, there is no bus and no trace:
 *  the target still gets its brackets, so nothing is lost. */

export type Tone = "live" | "attn" | "bad" | "ok" | "cool" | "mid";
type Pt = [number, number];
interface Trace {
  P: Pt[];
  t0: number;
  dur: number;
  tone: Tone;
  wall?: boolean;
}

// One beat for every motion: a 120ms unit; the trace leaves two units after the ripple.
export const BEAT = { u: 120, trace: 1.0 };
export const LAND_MS = BEAT.u + 2 * BEAT.u + BEAT.trace * 580;

const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const small = () => innerWidth < 600;
const now = () => performance.now() / 1000;
const cssv = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

let busCanvas: HTMLCanvasElement | null = null;
let fxCanvas: HTMLCanvasElement | null = null;
let traces: Trace[] = [];
let running = false;
let W = 0;
let H = 0;
const dpr = () => Math.min(2, devicePixelRatio || 1);
const TONE: Record<Tone, string> = { live: "", attn: "", bad: "", ok: "", cool: "", mid: "" };

function readTones() {
  Object.assign(TONE, {
    cool: cssv("--point-hi"),
    live: cssv("--color-accent"),
    attn: cssv("--color-attn"),
    bad: cssv("--color-error"),
    ok: cssv("--color-success"),
    mid: cssv("--point-mid"),
  });
}

const rectOf = (el: Element | null) => {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.width ? r : null;
};
const onScreen = (r: DOMRect | null) => !!r && r.bottom > 0 && r.top < H && r.right > 0 && r.left < W;

/** Close brackets on an element, in a tone, for a second. */
export function hit(el: Element | null, tone: Tone) {
  if (!el) return;
  el.classList.remove("is-hit", "hit-live", "hit-attn", "hit-bad", "hit-ok", "hit-cool");
  void (el as HTMLElement).offsetWidth;
  el.classList.add("is-hit", `hit-${tone}`);
  setTimeout(() => el.classList.remove("is-hit"), 1000);
}

// Sample a polyline every few pixels, so a trace is made of evenly spaced points.
function sample(pts: Pt[], step = 5): Pt[] {
  const out: Pt[] = [];
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1];
    const [x1, y1] = pts[i];
    const n = Math.max(1, Math.round(Math.hypot(x1 - x0, y1 - y0) / step));
    for (let k = i === 1 ? 0 : 1; k <= n; k++) out.push([x0 + ((x1 - x0) * k) / n, y0 + ((y1 - y0) * k) / n]);
  }
  return out;
}

// The lanes: the gap above the centre panel, the gap to its right (when the side column
// is beside it), and a lane down the thread's left gutter.
function lanes() {
  const core = rectOf(document.querySelector(".core"));
  const centre = rectOf(document.querySelector(".center"));
  const thread = document.querySelector<HTMLElement>(".thread");
  const side = rectOf(document.querySelector(".side"));
  const composer = rectOf(document.querySelector(".composer"));
  if (!core || !centre || !thread) return null;
  const th = thread.getBoundingClientRect();
  const cx = core.left + core.width / 2;
  const cy = core.top + core.height / 2;
  const R = Math.min(core.width, core.height) * 0.37;
  const right = side && side.left > centre.right ? centre.right + 6 : null;
  return {
    cx,
    cy,
    R,
    top: centre.top - 7,
    right,
    bottom: centre.bottom,
    lane: th.left + Math.max(6, parseFloat(getComputedStyle(thread).paddingLeft) / 2),
    laneBottom: (composer ? composer.top : centre.bottom) - 10,
    centre,
  };
}

let busK = 1;
/** Draw the bus, `k` of the way along (the opening grows it from 0 to 1). */
export function drawBus(k = busK) {
  busK = k;
  const c = busCanvas;
  if (!c) return;
  c.width = Math.round(W * dpr());
  c.height = Math.round(H * dpr());
  const g = c.getContext("2d");
  if (!g) return;
  g.setTransform(dpr(), 0, 0, dpr(), 0, 0);
  g.clearRect(0, 0, W, H);
  const L = lanes();
  if (!L || small() || (reduce() && k < 1)) return;
  const segs: Pt[][] = [[[L.cx, L.cy - L.R], [L.cx, L.top]]];
  if (L.right != null) segs.push([[L.cx, L.top], [L.right, L.top]], [[L.right, L.top], [L.right, L.bottom]]);
  // The live view covers the thread, so its lane is left out while it is open.
  const sheet = !!document.documentElement.dataset.hud;
  if (!sheet) segs.push([[L.cx - L.R, L.cy], [L.lane, L.cy], [L.lane, L.laneBottom]]);
  g.fillStyle = TONE.mid;
  for (const seg of segs) {
    const P = sample(seg, 6);
    const n = Math.round(P.length * k);
    g.globalAlpha = 0.38;
    for (let j = 0; j < n; j++) g.fillRect(Math.round(P[j][0] - 0.75), Math.round(P[j][1] - 0.75), 1.5, 1.5);
  }
  if (k >= 1) {
    g.globalAlpha = 0.7;
    const joints: Pt[] = sheet ? [[L.cx, L.top]] : [[L.cx, L.top], [L.lane, L.cy]];
    if (L.right != null) joints.push([L.right, L.top]);
    for (const [x, y] of joints) g.fillRect(Math.round(x - 2), Math.round(y - 2), 4, 4);
  }
  g.globalAlpha = 1;
}

// Out of the presence, along the bus, and in to the target.
function route(b: DOMRect): Pt[] | null {
  const L = lanes();
  if (!L) return null;
  const ey = b.top + b.height / 2;
  if (L.right != null && b.left >= L.centre.right - 2) {
    return [[L.cx, L.cy - L.R], [L.cx, L.top], [L.right, L.top], [L.right, ey], [b.left + 12, ey]];
  }
  return [[L.cx - L.R, L.cy], [L.lane, L.cy], [L.lane, ey], [b.left + 26, ey]];
}

/** Send a trace from the presence to an element; it lands with brackets. */
export function trace(to: Element | null, tone: Tone, opts: { dur?: number; land?: boolean } = {}) {
  const b = rectOf(to);
  const land = () => {
    if (opts.land !== false) hit(to, tone);
  };
  if (!b || reduce() || small() || !onScreen(b) || !onScreen(rectOf(document.querySelector(".core")))) return land();
  const pts = route(b);
  if (!pts) return land();
  const P = sample(pts, 4);
  const dur = opts.dur ?? Math.min(1.4, 0.75 + P.length * 0.0012);
  traces.push({ P, t0: now(), dur, tone });
  setTimeout(land, dur * 0.58 * 1000);
  wake();
}

/** Send work: the trace leaves two beats after the presence ripples. */
export function send(to: Element | null, tone: Tone, opts: { dur?: number; land?: boolean } = {}) {
  setTimeout(() => trace(to, tone, opts), reduce() ? 0 : 2 * BEAT.u);
}

/** A refusal: a short trace up out of the presence that stops at a wall. */
export function bounce(tone: Tone) {
  const c = rectOf(document.querySelector(".core"));
  if (!c || reduce() || small() || !onScreen(c)) return;
  const cx = c.left + c.width / 2;
  const cy = c.top + c.height / 2;
  const R = Math.min(c.width, c.height) * 0.37;
  const L = lanes();
  const top = L ? Math.max(L.top + 10, cy - R - 60) : cy - R - 40;
  traces.push({ P: sample([[cx, cy - R], [cx, top]], 4), t0: now(), dur: 1.3, tone, wall: true });
  wake();
}

function wake() {
  document.documentElement.dataset.flow = "1";
  if (!running) {
    running = true;
    requestAnimationFrame(frame);
  }
}

function frame() {
  const c = fxCanvas;
  const g = c?.getContext("2d");
  if (!c || !g) {
    running = false;
    return;
  }
  const t = now();
  g.setTransform(dpr(), 0, 0, dpr(), 0, 0);
  g.clearRect(0, 0, W, H);
  traces = traces.filter((tr) => (t - tr.t0) / tr.dur < 1);
  for (const tr of traces) {
    const k = (t - tr.t0) / tr.dur;
    const N = tr.P.length;
    const head = 1 - Math.pow(1 - Math.min(1, k / 0.58), 3);
    const tail = Math.max(0, (k - 0.62) / 0.38);
    const hi = Math.round(head * (N - 1));
    const lo = Math.round(tail * tail * (N - 1));
    g.fillStyle = TONE[tr.tone];
    for (let j = lo; j <= hi; j++) {
      const [x, y] = tr.P[j];
      const near = hi - j;
      g.globalAlpha = near < 4 ? 1 : near < 12 ? 0.62 : Math.max(0.22, 0.62 - (near - 12) / 90);
      const s = near < 4 ? 3 : 2;
      g.fillRect(Math.round(x - s / 2), Math.round(y - s / 2), s, s);
    }
    if (tr.wall && head >= 0.999) {
      const [x, y] = tr.P[N - 1];
      g.globalAlpha = 1 - tail;
      g.fillRect(Math.round(x - 10), Math.round(y - 5), 20, 2);
    }
  }
  g.globalAlpha = 1;
  if (traces.length) requestAnimationFrame(frame);
  else {
    running = false;
    g.clearRect(0, 0, W, H);
    delete document.documentElement.dataset.flow;
  }
}

/** Attach the overlay to its two canvases. Returns a function that detaches it. */
export function attachOverlay(bus: HTMLCanvasElement, fx: HTMLCanvasElement): () => void {
  busCanvas = bus;
  fxCanvas = fx;
  readTones();
  const resize = () => {
    W = innerWidth;
    H = innerHeight;
    fx.width = Math.round(W * dpr());
    fx.height = Math.round(H * dpr());
    drawBus();
  };
  resize();
  addEventListener("resize", resize);
  // Panels change size as content arrives; the bus follows the centre panel.
  const watch = new ResizeObserver(() => drawBus());
  const centre = document.querySelector(".center");
  if (centre) watch.observe(centre);
  document.fonts?.ready.then(() => drawBus());
  return () => {
    removeEventListener("resize", resize);
    watch.disconnect();
    busCanvas = fxCanvas = null;
    traces = [];
  };
}
