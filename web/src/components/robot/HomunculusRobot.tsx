import { useEffect, useRef } from "react";

/** HOMUNCULUS — PIXEL ROBOT (LED-cell build).
 *
 *  A drop-in replacement for the original smooth-canvas robot. The figure
 *  is drawn as a grid of phosphor LED cells — the SAME visual language as
 *  DotMatrixWordmark — so it reads as "the same display, awake" rather than
 *  a mascot pasted on top.
 *
 *  Public API is unchanged: same exports, same props. `useRobotState.ts`
 *  and every call site keep working with no edits. `noDust` / `filled`
 *  are accepted for compatibility but are no-ops in the pixel build.
 *
 *  States: boot | idle | listening | thinking | working | responding |
 *          success | error
 */
export type RobotState =
  | "boot" | "idle" | "listening" | "thinking" | "working"
  | "responding" | "success" | "error";

export type RobotDetail = "high" | "mid" | "low";
export type RobotPalette = "phosphor" | "cream" | "amber" | "cyan" | "white";

interface Props {
  state: RobotState;
  detail?: RobotDetail;
  palette?: RobotPalette;
  /** accepted for API compat — no-op in the pixel build */
  noDust?: boolean;
  /** accepted for API compat — no-op in the pixel build */
  filled?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

/** base = structure/dim cells, hot = bright/glowing cells (rgb triples). */
const PALETTES: Record<RobotPalette, { base: string; hot: string }> = {
  phosphor: { base: "124,254,0",   hot: "200,255,140" }, // app accent #7CFE00
  cream:    { base: "232,224,200", hot: "255,250,232" },
  amber:    { base: "255,176,0",   hot: "255,214,128" },
  cyan:     { base: "0,212,255",   hot: "180,240,255" },
  white:    { base: "230,230,230", hot: "255,255,255" },
};
const FAULT = { base: "255,176,0", hot: "255,214,128" }; // amber fault

// ── shared cursor tracker (module-level, attached once) ──────────────
const cursor = { x: 0, y: 0, last: 0 };
let cursorAttached = false;
function attachCursor() {
  if (cursorAttached || typeof window === "undefined") return;
  cursorAttached = true;
  cursor.x = window.innerWidth / 2;
  cursor.y = window.innerHeight / 2;
  window.addEventListener("mousemove", (e) => {
    cursor.x = e.clientX; cursor.y = e.clientY; cursor.last = performance.now();
  });
}
function cursorOffset(canvas: HTMLCanvasElement) {
  const r = canvas.getBoundingClientRect();
  if (!r.width) return { nx: 0, ny: 0, dist: Infinity, recent: false };
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const dx = cursor.x - cx, dy = cursor.y - cy;
  return {
    nx: Math.max(-1, Math.min(1, dx / 520)),
    ny: Math.max(-1, Math.min(1, dy / 400)),
    dist: Math.hypot(dx, dy),
    recent: performance.now() - cursor.last < 4500,
  };
}
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const hash = (n: number) => { const s = Math.sin(n * 127.1) * 43758.5453; return s - Math.floor(s); };

const COLS = 23, ROWS = 26;

type Pose = {
  eyeShape: "circle" | "x" | "crescent";
  eyeLook: [number, number];
  arms: "rest" | "up" | "work";
  antenna: number;
  treadPhase: number;
  speak: number;
  bootPhase?: number;
  showThought?: boolean;
  showEars?: boolean;
  showSpeech?: boolean;
};

export function HomunculusRobot({
  state, detail = "high", palette = "phosphor", className, style,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<RobotState>(state);
  const prevRef = useRef<RobotState>(state);

  useEffect(() => { prevRef.current = stateRef.current; stateRef.current = state; }, [state]);

  useEffect(() => {
    attachCursor();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let t = 0, bootT = 0, transition = 1;
    let lastState = stateRef.current;
    let lastNonIdle: RobotState | null = null, lastNonIdleEnd = -10;
    let lookTarget: [number, number] = [0, 0];
    let lookCur: [number, number] = [0, 0];
    let nextSaccade = 1, saccadeStart = 0;
    let blinkUntil = 0, nextBlink = 2 + Math.random() * 3;
    let eyeOpenL = detail === "high" ? 0 : 1;
    let eyeOpenR = detail === "high" ? 0 : 1;
    const quirk = Math.floor(Math.random() * 3);
    const cells = new Float32Array(COLS * ROWS);

    const set = (c: number, r: number, b: number) => {
      if (c >= 0 && c < COLS && r >= 0 && r < ROWS) { const i = r * COLS + c; if (b > cells[i]) cells[i] = b; }
    };
    const clear = (c: number, r: number) => { if (c >= 0 && c < COLS && r >= 0 && r < ROWS) cells[r * COLS + c] = 0; };
    const panel = (c0: number, r0: number, w: number, h: number, bFill: number, bEdge: number, round: number) => {
      for (let r = r0; r < r0 + h; r++) for (let c = c0; c < c0 + w; c++) {
        const corner =
          (c < c0 + round && r < r0 + round && (c0 + round - 1 - c) + (r0 + round - 1 - r) >= round) ||
          (c >= c0 + w - round && r < r0 + round && (c - (c0 + w - round)) + (r0 + round - 1 - r) >= round) ||
          (c < c0 + round && r >= r0 + h - round && (c0 + round - 1 - c) + (r - (r0 + h - round)) >= round) ||
          (c >= c0 + w - round && r >= r0 + h - round && (c - (c0 + w - round)) + (r - (r0 + h - round)) >= round);
        if (corner) continue;
        const edge = c === c0 || c === c0 + w - 1 || r === r0 || r === r0 + h - 1;
        set(c, r, edge ? bEdge : bFill);
      }
    };

    const computePose = (): Pose => {
      const cur = stateRef.current;
      const pose: Pose = { eyeShape: "circle", eyeLook: [lookCur[0], lookCur[1]], arms: "rest", antenna: 0.4, treadPhase: 0, speak: 0 };
      switch (cur) {
        case "boot": pose.eyeLook = [0, 0]; pose.antenna = 1; break;
        case "idle": pose.antenna = 0.35 + Math.sin(t * 1.5) * 0.1; break;
        case "listening": pose.eyeLook = [lookCur[0] * 0.5, -0.5]; pose.antenna = 0.85; pose.showEars = true; break;
        case "thinking": pose.eyeLook = [0.8, -1]; pose.antenna = 0.9; pose.treadPhase = Math.sin(t * 0.6); pose.showThought = true; break;
        case "working": pose.eyeLook = [0, 1]; pose.arms = "work"; pose.antenna = 0.6; break;
        case "responding": pose.eyeLook = [0, 0]; pose.antenna = 0.85; pose.showSpeech = true; pose.speak = (Math.sin(t * 6) + 1) * 0.5; break;
        case "success": pose.eyeShape = "crescent"; pose.arms = "up"; pose.antenna = 1; break;
        case "error": pose.eyeShape = "x"; pose.antenna = 0.3; break;
      }
      if (cur === "idle" && lastNonIdle) {
        const since = performance.now() / 1000 - lastNonIdleEnd;
        if (since < 3) {
          const f = 1 - since / 3;
          if (lastNonIdle === "error") { pose.antenna *= 1 - 0.4 * f; pose.eyeLook = [0, 0.4 * f]; }
          else if (lastNonIdle === "success") pose.antenna = Math.max(pose.antenna, 0.5 + 0.4 * f);
        }
      }
      return pose;
    };

    const updateLook = () => {
      const cur = stateRef.current;
      const c = cursorOffset(canvas);
      const allow = cur === "idle" || cur === "listening" || cur === "responding" || cur === "boot";
      const active = c.recent && c.dist < 800 && allow;
      if (active) {
        const tx = c.nx * 1.0, ty = c.ny * 0.8;
        if (Math.abs(lookTarget[0] - tx) > 0.2 || Math.abs(lookTarget[1] - ty) > 0.2) saccadeStart = t;
        lookTarget = [tx, ty]; nextSaccade = t + 1.5;
      } else if (t > nextSaccade && allow) {
        const a = Math.random() * Math.PI * 2, r = 0.3 + Math.random() * 0.7;
        lookTarget = [Math.cos(a) * r, Math.sin(a) * r * 0.6]; saccadeStart = t; nextSaccade = t + 1.2 + Math.random() * 2.5;
      }
      const since = t - saccadeStart;
      const rate = since < 0.1 ? 0.6 : since < 0.3 ? 0.25 : 0.08;
      lookCur[0] = lerp(lookCur[0], lookTarget[0], rate);
      lookCur[1] = lerp(lookCur[1], lookTarget[1], rate);
    };

    const buildSprite = (pose: Pose) => {
      const cur = stateRef.current;
      cells.fill(0);
      const dim = 0.3, edge = 0.85, body = 0.26, bodyEdge = 0.7;
      const bp = pose.bootPhase ?? 1;
      const show = bp > 0.05;

      if (bp > 0.12) { set(11, 2, dim); set(11, 1, dim * 0.8); set(11, 0, 0.7 + (pose.antenna ?? 0.5) * 0.3); }
      if (show) panel(3, 3, 16, 11, 0.1, edge, 2);

      const drawEye = (cx: number, cy: number) => {
        if (pose.eyeShape === "x") { for (let k = -2; k <= 2; k++) { set(cx + k, cy + k, 1); set(cx + k, cy - k, 1); } return; }
        if (pose.eyeShape === "crescent") {
          set(cx - 2, cy, 0.9); set(cx - 1, cy + 1, 1); set(cx, cy + 1, 1); set(cx + 1, cy + 1, 1); set(cx + 2, cy, 0.9); return;
        }
        const open = cx === 7 ? eyeOpenL : eyeOpenR;
        if (open < 0.18) { for (let k = -2; k <= 2; k++) set(cx + k, cy, 0.95); return; }
        const sq = open < 0.6 ? 0.5 : 1;
        for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) {
          const d = Math.hypot(c - cx, (r - cy) / sq);
          if (d <= 2.7) set(c, r, d > 2.0 ? 1 : 0.82);
        }
        const lx = Math.max(-1, Math.min(1, pose.eyeLook[0]));
        const ly = Math.max(-1, Math.min(1, pose.eyeLook[1]));
        const px = cx + Math.round(lx), py = cy + Math.round(ly * sq);
        clear(px, py); clear(px + 1, py);
        if (open >= 0.6) { clear(px, py + 1); clear(px + 1, py + 1); }
        set(cx - 1, cy - 2, 1);
      };
      if (show) { drawEye(7, 8); drawEye(15, 8); }

      if (show) {
        for (let c = 8; c <= 14; c++) {
          let b = pose.showSpeech ? 0.4 + (Math.sin(t * 9 + c * 0.7) * 0.5 + 0.5) * (pose.speak + 0.4) : 0.18 + Math.sin(t * 0.6 + c) * 0.04;
          set(c, 11, Math.min(1, b));
        }
      }
      if (show) for (let c = 9; c <= 13; c++) set(c, 14, 0.5);

      if (show) {
        panel(5, 15, 13, 7, body, bodyEdge, 2);
        for (let r = 17; r <= 19; r++) for (let c = 8; c <= 14; c++) cells[r * COLS + c] = 0.08;
        [9, 11, 13].forEach((c, i) => {
          let on = cur === "working" ? Math.sin(t * 4 + i) > 0 : i === Math.floor(t * 2) % 3;
          if (i === quirk) on = hash(Math.floor(t * 5) + i) > 0.5;
          set(c, 18, on ? 1 : 0.16);
        });
        if (pose.arms === "up") { set(4, 14, 0.6); set(4, 13, 0.6); set(18, 14, 0.6); set(18, 13, 0.6); }
        else if (pose.arms === "work") { set(4, 17, 0.6); set(5, 18, 0.6); set(18, 17, 0.6); set(17, 18, 0.6); }
        else { for (let r = 16; r <= 18; r++) { set(4, r, 0.55); set(18, r, 0.55); } }
        panel(5, 22, 13, 2, dim, 0.6, 1);
        for (let w = 0; w < 3; w++) set(7 + w * 4, 22, 0.7);
      }
    };

    const render = () => {
      const rect = canvas.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) { raf = requestAnimationFrame(render); return; }
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const tw = Math.max(2, Math.floor(rect.width * dpr)), th = Math.max(2, Math.floor(rect.height * dpr));
      if (canvas.width !== tw || canvas.height !== th) { canvas.width = tw; canvas.height = th; }
      const W = canvas.width, H = canvas.height;
      if (W < 4 || H < 4) { raf = requestAnimationFrame(render); return; }

      t += 0.016;
      const cur = stateRef.current, prev = prevRef.current;
      if (cur === "boot") bootT += 0.016;
      if (cur !== lastState) {
        if (cur === "idle" && lastState !== "idle" && lastState !== "boot") { lastNonIdle = lastState; lastNonIdleEnd = performance.now() / 1000; }
        lastState = cur; transition = 0;
      }
      transition = Math.min(1, transition + 0.05);

      const isErr = cur === "error";
      const pal = isErr ? FAULT : PALETTES[palette];
      const base = pal.base, hot = pal.hot;

      ctx.fillStyle = `rgba(5,5,5,${detail === "high" ? 0.55 : 0.85})`;
      ctx.fillRect(0, 0, W, H);

      updateLook();
      const pose = computePose();

      let tL = 1, tR = 1;
      if (cur === "boot") {
        const phase = Math.min(1, bootT / 2.3);
        pose.bootPhase = phase; pose.antenna = phase > 0.12 ? 1 : 0;
        tL = phase > 0.45 ? 1 : 0; tR = phase > 0.58 ? 1 : 0;
      } else {
        if (t > nextBlink) { blinkUntil = t + 0.13; nextBlink = t + 2 + Math.random() * 3.5; }
        tL = tR = t < blinkUntil ? 0 : 1;
        if (transition < 0.28 && prev !== "boot") tL = tR = 0;
      }
      eyeOpenL = lerp(eyeOpenL, tL, 0.4);
      eyeOpenR = lerp(eyeOpenR, tR, 0.4);

      buildSprite(pose);

      const pad = detail === "high" ? 0.07 : 0.05;
      const cs = Math.min(W * (1 - pad * 2) / COLS, H * (1 - pad * 2) / ROWS);
      const ox = (W - cs * COLS) / 2, oy = (H - cs * ROWS) / 2;
      const gap = Math.max(0.6, cs * 0.13);
      const scan = ((t * 0.35) % 1.5) * ROWS - 1;

      for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) {
        let b = cells[r * COLS + c]; if (!b) continue;
        b *= 0.86 + 0.14 * hash(c * 7.3 + r * 3.1 + Math.floor(t * 8) * 0.5);
        if (Math.abs(r - scan) < 1) b = Math.min(1, b + 0.22);
        const x = ox + c * cs, y = oy + r * cs;
        if (b > 0.6) { ctx.fillStyle = `rgba(${hot},${Math.min(1, b)})`; if (detail !== "low") { ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = cs * 0.85; } }
        else { ctx.fillStyle = `rgba(${base},${b})`; ctx.shadowBlur = 0; }
        ctx.fillRect(x + gap, y + gap, cs - gap * 2, cs - gap * 2);
        ctx.shadowBlur = 0;
      }

      // overlays
      if (pose.showThought && detail !== "low") {
        for (let i = 0; i < 3; i++) { const ph = (t * 1.3 + i * 0.45) % 2; const a = ph < 1 ? ph : 2 - ph;
          ctx.fillStyle = `rgba(${hot},${a * 0.9})`; ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = cs * 0.8;
          ctx.fillRect(ox + (18 + i * 0.8) * cs, oy + (3 - i * 1.1) * cs, cs * 0.8, cs * 0.8); ctx.shadowBlur = 0; }
      }
      if (pose.showEars && detail !== "low") {
        for (let side = -1; side <= 1; side += 2) for (let i = 0; i < 3; i++) { const ph = (t * 1.8 + i * 0.4) % 1.6; if (ph > 1.4) continue;
          const a = 1 - ph / 1.4, cc = (side < 0 ? 1.5 : 20.5) + side * (-i * 0.9);
          ctx.fillStyle = `rgba(${base},${a * 0.8})`; ctx.fillRect(ox + cc * cs, oy + 8 * cs, cs * 0.7, cs * 0.7); }
      }
      if (cur === "success" && detail !== "low") {
        for (let i = 0; i < 7; i++) { const life = (t * 1.5 + i * 0.6) % 2; if (life > 1.3) continue;
          const ang = i / 7 * Math.PI * 2, d = life * cs * 6;
          ctx.fillStyle = `rgba(${hot},${1 - life / 1.3})`; ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = cs * 0.8;
          ctx.fillRect(ox + 11 * cs + Math.cos(ang) * d, oy + 8 * cs + Math.sin(ang) * d - life * cs * 2, cs * 0.7, cs * 0.7); ctx.shadowBlur = 0; }
      }
      if (cur === "error" && detail !== "low" && Math.sin(t * 6) > 0) {
        ctx.fillStyle = `rgba(${base},0.9)`; ctx.fillRect(ox + 11 * cs, oy + 0.3 * cs, cs * 0.8, cs * 0.8);
      }

      raf = requestAnimationFrame(render);
    };
    raf = requestAnimationFrame(render);
    return () => cancelAnimationFrame(raf);
  }, [detail, palette]);

  return <canvas ref={canvasRef} className={className} style={style} />;
}
