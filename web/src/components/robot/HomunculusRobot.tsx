import { useEffect, useRef } from "react";

/** HOMUNCULUS LIVING ROBOT — ported from the v2 design.
 *
 *  Renders a phosphor-green robot character to a <canvas>. Three
 *  detail tiers: `high` (Landing hero — dust motes, glow, boot
 *  sequence), `mid` (sidebar avatar — most details, no boot), `low`
 *  (chat avatar — minimal). Tracks the global mouse, performs
 *  saccade eye movements, and reacts to robot-state changes set by
 *  the parent.
 *
 *  States: boot | idle | listening | thinking | working | responding |
 *          success | error
 */
export type RobotState =
  | "boot" | "idle" | "listening" | "thinking" | "working"
  | "responding" | "success" | "error";

export type RobotDetail = "high" | "mid" | "low";

/** Pre-set color schemes for the robot's body. The `state` still
 *  drives pose/animation; this just changes the rendered hue. */
export type RobotPalette = "phosphor" | "cream" | "amber" | "cyan" | "white";

interface Props {
  state: RobotState;
  detail?: RobotDetail;
  /** Body / line color. Defaults to phosphor (matches app accent). */
  palette?: RobotPalette;
  /** disable the dust motes layer even on `high` (use for inline contexts) */
  noDust?: boolean;
  /** Fill the body & head with a denser phosphor tint instead of near-black.
   *  Makes the robot read as a solid silhouette rather than a line drawing. */
  filled?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

const PALETTES: Record<RobotPalette, { base: string; hot: string }> = {
  phosphor: { base: "109,255,58",   hot: "200,255,153" }, // green
  cream:    { base: "232,224,200",  hot: "255,250,232" }, // bone / parchment
  amber:    { base: "240,193,75",   hot: "255,231,168" },
  cyan:     { base: "0,212,255",    hot: "180,240,255" },
  white:    { base: "230,230,230",  hot: "255,255,255" },
};

// ── shared cursor tracker ────────────────────────────────────────
const cursor = { x: 0, y: 0, lastMove: 0 };
let cursorListenerAttached = false;
function attachCursorListener() {
  if (cursorListenerAttached || typeof window === "undefined") return;
  cursorListenerAttached = true;
  cursor.x = window.innerWidth / 2;
  cursor.y = window.innerHeight / 2;
  window.addEventListener("mousemove", (e) => {
    cursor.x = e.clientX;
    cursor.y = e.clientY;
    cursor.lastMove = performance.now();
  });
}

function cursorOffsetFor(canvas: HTMLCanvasElement) {
  const r = canvas.getBoundingClientRect();
  if (r.width === 0) return { dx: 0, dy: 0, normX: 0, normY: 0, dist: Infinity, recent: false };
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  const dx = cursor.x - cx;
  const dy = cursor.y - cy;
  const dist = Math.hypot(dx, dy);
  const normX = Math.max(-1, Math.min(1, dx / 480));
  const normY = Math.max(-1, Math.min(1, dy / 360));
  const recent = (performance.now() - cursor.lastMove) < 4500;
  return { dx, dy, normX, normY, dist, recent };
}

function lerp(a: number, b: number, t: number) { return a + (b - a) * t; }
function noise1(x: number, seed = 0) {
  const xi = Math.floor(x), xf = x - xi;
  const r = (i: number) => { const s = Math.sin(i * 12.9898 + seed * 78.233) * 43758.5453; return s - Math.floor(s); };
  const u = xf * xf * (3 - 2 * xf);
  return r(xi) * (1 - u) + r(xi + 1) * u;
}
function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number | number[]) {
  const rr = typeof r === "number" ? [r, r, r, r] : r;
  ctx.beginPath();
  ctx.moveTo(x + rr[0], y);
  ctx.lineTo(x + w - rr[1], y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + rr[1]);
  ctx.lineTo(x + w, y + h - rr[2]);
  ctx.quadraticCurveTo(x + w, y + h, x + w - rr[2], y + h);
  ctx.lineTo(x + rr[3], y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - rr[3]);
  ctx.lineTo(x, y + rr[0]);
  ctx.quadraticCurveTo(x, y, x + rr[0], y);
  ctx.closePath();
}

// ── pose data ────────────────────────────────────────────────────
const STATES: Record<RobotState, { hue: "green" | "amber" }> = {
  boot:       { hue: "green" },
  idle:       { hue: "green" },
  listening:  { hue: "green" },
  thinking:   { hue: "green" },
  working:    { hue: "green" },
  responding: { hue: "green" },
  success:    { hue: "green" },
  error:      { hue: "amber" },
};

type Pose = {
  headPan: number; headTilt: number; bodyBob: number;
  eyeDilation: number; eyeOpen: number;
  eyeOpenL?: number; eyeOpenR?: number;
  eyeLook: [number, number];
  eyeShape: "circle" | "x" | "crescent";
  armL: "rest" | "up" | "chin" | "gesture" | "work";
  armR: "rest" | "up" | "chin" | "gesture" | "work";
  antennaPulse: number;
  treadPhase?: number;
  bootPhase?: number;
  bootBodyOn?: boolean;
  showThought?: boolean;
  showEars?: boolean;
  showCode?: boolean;
  showSpeech?: boolean;
  showSparks?: boolean;
  showSparkErr?: boolean;
  showWarn?: boolean;
  speakIntensity?: number;
  typingPhase?: number;
};

function poseFor(state: RobotState, t: number, bootT: number): Pose {
  const blink = (Math.sin(t * 1.4 + 0.2) > 0.97 || Math.sin(t * 1.4 + 4.2) > 0.97) ? 0.08 : 1;
  // Idle gets a deliberate mechanical blink every ~5s instead of the
  // organic random one, so it reads as "machine cycling" rather than
  // dead. 200ms full closure, sharp open.
  const idleCycle = t % 5.0;
  const idleBlink = idleCycle < 0.20 ? Math.max(0.05, idleCycle / 0.10 - 1)
                  : idleCycle < 0.25 ? (idleCycle - 0.20) / 0.05
                  : 1;
  switch (state) {
    case "boot": {
      const phase = Math.min(1, bootT / 2.5);
      const antennaOn = phase > 0.12 ? Math.min(1, (phase - 0.12) / 0.12) : 0;
      const leftEyeOpen = phase > 0.42 ? Math.min(1, (phase - 0.42) / 0.10) : 0;
      const rightEyeOpen = phase > 0.55 ? Math.min(1, (phase - 0.55) / 0.10) : 0;
      const stretchT = Math.max(0, Math.min(1, (phase - 0.78) / 0.18));
      const stretch = Math.sin(stretchT * Math.PI) * 0.10;
      return { headPan: 0, headTilt: -stretch, bodyBob: 0, eyeDilation: 0.9 + 0.1 * leftEyeOpen, eyeOpen: 1, eyeOpenL: leftEyeOpen, eyeOpenR: rightEyeOpen, eyeLook: [0,0], eyeShape: "circle", armL: "rest", armR: "rest", antennaPulse: antennaOn, treadPhase: 0, bootPhase: phase, bootBodyOn: phase > 0.05 };
    }
    // Mechanical 4s breathing cycle (visibly bobbing, not whisper subtle)
    // + scheduled blink so the idle reads as a machine cycling, not dead.
    case "idle":       return { headPan:0, headTilt:Math.sin(t*1.5708)*0.015, bodyBob:Math.sin(t*1.5708)*0.018, eyeDilation:1, eyeOpen:idleBlink, eyeLook:[0,0], eyeShape:"circle", armL:"rest", armR:"rest", antennaPulse:0.30 + Math.sin(t*1.5708)*0.10, treadPhase:0 };
    case "listening":  return { headPan:0, headTilt:-0.18, bodyBob:0, eyeDilation:1.55, eyeOpen:blink, eyeLook:[0.05,-0.10], eyeShape:"circle", armL:"rest", armR:"rest", antennaPulse:0.85, treadPhase:0, showEars:true };
    case "thinking":   return { headPan:0, headTilt:0.10, bodyBob:0, eyeDilation:0.9, eyeOpen:blink, eyeLook:[0.55,-0.75], eyeShape:"circle", armL:"rest", armR:"chin", antennaPulse:0.95, treadPhase:Math.sin(t*0.6), showThought:true };
    case "working":    return { headPan:0, headTilt:0.32, bodyBob:0, eyeDilation:1, eyeOpen:blink, eyeLook:[0,0.75], eyeShape:"circle", armL:"work", armR:"work", antennaPulse:0.65, typingPhase:t*8, treadPhase:0, showCode:true };
    case "responding": return { headPan:0, headTilt:-0.04, bodyBob:Math.sin(t*4)*0.004, eyeDilation:1.15, eyeOpen:blink, eyeLook:[0,0], eyeShape:"circle", armL:"rest", armR:"gesture", antennaPulse:0.9, treadPhase:0, showSpeech:true, speakIntensity:(Math.sin(t*6)+1)*0.5 };
    case "success":    return { headPan:0, headTilt:-0.22, bodyBob:Math.max(0,Math.sin(t*4))*0.024, eyeDilation:1.2, eyeOpen:1, eyeLook:[0,-0.1], eyeShape:"crescent", armL:"up", armR:"up", antennaPulse:1, treadPhase:0, showSparks:true };
    case "error":      return { headPan:0, headTilt:0.38, bodyBob:Math.sin(t*30)*0.003, eyeDilation:0.65, eyeOpen:blink, eyeLook:[0,0.45], eyeShape:"x", armL:"rest", armR:"rest", antennaPulse:0.30, treadPhase:0, showSparkErr:true, showWarn:true };
  }
}

export function HomunculusRobot({ state, detail = "high", palette = "phosphor", noDust, filled, className, style }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<RobotState>(state);
  const prevStateRef = useRef<RobotState>(state);

  // keep latest state in a ref so the rAF loop sees it without re-mounting
  useEffect(() => {
    prevStateRef.current = stateRef.current;
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    attachCursorListener();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let t = 0;
    let bootT = 0;
    let lastState: RobotState = stateRef.current;
    let transition = 1;
    const sparks: Array<{ x:number; y:number; vx:number; vy:number; life:number; max:number; size:number }> = [];
    const dust: Array<{ x:number; y:number; vx:number; vy:number; life:number; max:number; r:number }> = [];
    let lookTarget: [number, number] = [0,0];
    let lookCurrent: [number, number] = [0,0];
    let nextSaccade = 0.8 + Math.random() * 1.2;
    let saccadeStart = 0;
    let eyeOpenLSmoothed = 1;
    let eyeOpenRSmoothed = 1;
    const quirkSeed = Math.random();
    const brokenLedIdx = Math.floor(quirkSeed * 5);
    let lastNonIdleState: RobotState | null = null;
    let lastNonIdleEndT = -10;
    let dpr = Math.min(2, window.devicePixelRatio || 1);

    const resize = () => {
      const r = canvas.getBoundingClientRect();
      dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.max(2, Math.floor(r.width * dpr));
      canvas.height = Math.max(2, Math.floor(r.height * dpr));
    };
    resize();
    window.addEventListener("resize", resize);

    const render = () => {
      const rect = canvas.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        raf = requestAnimationFrame(render);
        return;
      }
      // dpr-aware resize on every frame in case layout shifts
      const targetW = Math.max(2, Math.floor(rect.width * dpr));
      const targetH = Math.max(2, Math.floor(rect.height * dpr));
      if (canvas.width !== targetW || canvas.height !== targetH) {
        canvas.width = targetW; canvas.height = targetH;
      }
      const W = canvas.width, H = canvas.height;
      if (W < 4 || H < 4) { raf = requestAnimationFrame(render); return; }
      t += 0.016;
      const currentState = stateRef.current;
      const prevState = prevStateRef.current;
      if (currentState === "boot") bootT += 0.016;

      // state transition
      if (currentState !== lastState) {
        // aftermath tracking
        if (currentState === "idle" && lastState !== "idle" && lastState !== "boot") {
          lastNonIdleState = lastState;
          lastNonIdleEndT = performance.now() / 1000;
        }
        lastState = currentState;
        transition = 0;
        if (currentState === "success" && detail !== "low") {
          for (let i = 0; i < 24; i++) {
            sparks.push({ x: W*0.5 + (Math.random()-0.5)*W*0.15, y: H*0.4, vx: (Math.random()-0.5)*4, vy: -2 - Math.random()*3, life: 0, max: 50 + Math.random()*30, size: 1 + Math.random()*1.5 });
          }
        }
      }
      transition = Math.min(1, transition + 0.05);

      // Error needs a color that contrasts with the body palette so it
      // reads as "alert" rather than "more of the same." Amber body
      // would clash with amber error, so step up to plasma red.
      const isErr = STATES[currentState].hue === "amber";
      const errorPalette: RobotPalette =
        palette === "amber" || palette === "cream" || palette === "white"
          ? "phosphor" // will be overridden below — placeholder
          : "amber";
      const PLASMA = { base: "255,77,46", hot: "255,170,140" };
      const pal = isErr
        ? (palette === "amber" || palette === "cream" || palette === "white"
            ? PLASMA
            : PALETTES.amber)
        : PALETTES[palette];
      void errorPalette;
      const base = pal.base;
      const hot = pal.hot;

      // fade
      ctx.fillStyle = `rgba(5,8,5,${detail === "high" ? 0.3 : 0.55})`;
      ctx.fillRect(0, 0, W, H);

      // ambient dust (high detail only)
      if (detail === "high" && !noDust) {
        if (dust.length < 28 && Math.random() < 0.05) {
          dust.push({ x: Math.random()*W, y: H + Math.random()*20, vx: (Math.random()-0.5)*0.25, vy: -0.12 - Math.random()*0.25, life: 0, max: 700 + Math.random()*500, r: 0.6 + Math.random()*0.9 });
        }
        for (let i = dust.length - 1; i >= 0; i--) if (dust[i].life >= dust[i].max || dust[i].y < -20) dust.splice(i, 1);
        for (const d of dust) {
          d.x += d.vx + Math.sin((t + d.life*0.012)*1.4)*0.18;
          d.y += d.vy;
          d.life++;
          const fadeIn = Math.min(1, d.life/60);
          const fadeOut = Math.max(0, 1 - d.life/d.max);
          ctx.fillStyle = `rgba(${base},${fadeIn * fadeOut * 0.4})`;
          ctx.beginPath();
          ctx.arc(d.x, d.y, d.r, 0, Math.PI*2);
          ctx.fill();
        }
      }

      // pose interpolation
      const curPose = poseFor(currentState, t, bootT);
      const prevPose = poseFor(prevState, t, bootT);
      const poseRaw: Record<string, unknown> = {};
      for (const k in curPose) {
        const c = (curPose as Record<string, unknown>)[k];
        const p = (prevPose as Record<string, unknown>)[k];
        if (Array.isArray(c)) {
          const pa = Array.isArray(p) ? p : c;
          poseRaw[k] = [lerp(pa[0] as number, c[0] as number, transition), lerp(pa[1] as number, c[1] as number, transition)];
        } else if (typeof c === "number") {
          poseRaw[k] = lerp(typeof p === "number" ? p : c, c, transition);
        } else {
          poseRaw[k] = transition > 0.5 ? c : (p ?? c);
        }
      }
      const pose = poseRaw as unknown as Pose;
      if (transition < 0.3 && prevState !== "boot") {
        pose.eyeOpen = Math.min(pose.eyeOpen, Math.max(0.05, transition * 3.5));
      }

      // cursor + saccade
      const c = cursorOffsetFor(canvas);
      const stateAllowsLook = (currentState === "idle" || currentState === "listening" || currentState === "responding");
      const cursorActive = c.recent && c.dist < 700 && stateAllowsLook;
      if (cursorActive) {
        const tx = c.normX * 1.05;
        const ty = c.normY * 0.75;
        if (Math.abs(lookTarget[0] - tx) > 0.15 || Math.abs(lookTarget[1] - ty) > 0.15) saccadeStart = t;
        lookTarget = [tx, ty];
        nextSaccade = t + 1.5;
      } else if (t > nextSaccade) {
        const angle = Math.random() * Math.PI * 2;
        const r = 0.2 + Math.random() * 0.75;
        lookTarget = [Math.cos(angle) * r, Math.sin(angle) * r * 0.6];
        saccadeStart = t;
        nextSaccade = t + 1.2 + Math.random() * 2.6;
      }
      const sinceSaccade = t - saccadeStart;
      const rate = sinceSaccade < 0.10 ? 0.55 : (sinceSaccade < 0.30 ? 0.20 : 0.07);
      lookCurrent[0] = lerp(lookCurrent[0], lookTarget[0], rate);
      lookCurrent[1] = lerp(lookCurrent[1], lookTarget[1], rate);
      if (stateAllowsLook) {
        pose.eyeLook = [lookCurrent[0], lookCurrent[1]];
        if (cursorActive && currentState === "idle") {
          pose.headPan = lerp(pose.headPan, c.normX * 0.45, 0.7);
          pose.headTilt = lerp(pose.headTilt, c.normY * 0.08, 0.5);
        }
      }

      // aftermath
      if (currentState === "idle" && lastNonIdleState) {
        const nowSec = performance.now() / 1000;
        const since = nowSec - lastNonIdleEndT;
        if (since < 3.5) {
          const fade = Math.max(0, 1 - since/3.5);
          if (lastNonIdleState === "error") { pose.headTilt += 0.10*fade; pose.eyeDilation *= (1 - 0.18*fade); pose.antennaPulse = Math.max(0.1, pose.antennaPulse - 0.15*fade); }
          else if (lastNonIdleState === "success") { pose.headTilt -= 0.06*fade; pose.antennaPulse = Math.max(pose.antennaPulse, 0.4 + 0.45*fade); pose.eyeDilation = Math.max(pose.eyeDilation, 1.0 + 0.15*fade); }
          else if (lastNonIdleState === "thinking") pose.headTilt += 0.03*fade;
          else if (lastNonIdleState === "working")  pose.headTilt += 0.04*fade;
        }
      }

      // per-eye smoothing (boot eye-open sequence)
      const tgtL = pose.eyeOpenL !== undefined ? pose.eyeOpenL * pose.eyeOpen : pose.eyeOpen;
      const tgtR = pose.eyeOpenR !== undefined ? pose.eyeOpenR * pose.eyeOpen : pose.eyeOpen;
      eyeOpenLSmoothed = lerp(eyeOpenLSmoothed, tgtL, 0.30);
      eyeOpenRSmoothed = lerp(eyeOpenRSmoothed, tgtR, 0.30);

      // ─── PROPORTIONS ───
      const F = Math.min(H * (detail === "low" ? 0.78 : 0.66), W * 0.85);
      const PROP = { bodyW:0.62, bodyH:0.40, neckH:0.04, neckW:0.20, headW:0.66, headH:0.32, armW:0.08, armLen:0.30, treadW:0.78, treadH:0.14, antenna:0.13 };
      const groundY = H * (detail === "low" ? 0.93 : 0.88);
      let ax = W/2;
      const ay = groundY - F * PROP.treadH/2;
      if (pose.treadPhase) ax += pose.treadPhase * 0.02 * F;
      const bobY = (pose.bodyBob || 0) * F;
      const lift = (currentState === "success") ? Math.max(0, Math.sin(t*4))*0.02*F : 0;

      const treadTop = ay - F * PROP.treadH/2;
      const treadBot = ay + F * PROP.treadH/2;
      const bodyW = F * PROP.bodyW;
      const bodyH = F * PROP.bodyH;
      const bodyTop = treadTop - bodyH + bobY - lift;
      const bodyBottom = treadTop + bobY - lift;
      const bodyLeft = ax - bodyW/2;
      const neckW = F * PROP.neckW;
      const neckH = F * PROP.neckH;
      const neckTop = bodyTop - neckH;
      const neckLeft = ax - neckW/2;
      const headW = F * PROP.headW;
      const headH = F * PROP.headH;
      const headPan = pose.headPan * 0.5 * F * 0.04;
      const headTilt = pose.headTilt;
      const headPivotX = ax;
      const headPivotY = neckTop;
      const sw = Math.max(1.2, F * 0.008);
      const swThick = sw * 1.4;

      // shadow
      if (detail !== "low") {
        const sg = ctx.createRadialGradient(ax, groundY, 0, ax, groundY, F*0.36);
        sg.addColorStop(0, `rgba(${base},0.22)`);
        sg.addColorStop(1, `rgba(${base},0)`);
        ctx.fillStyle = sg;
        ctx.beginPath(); ctx.ellipse(ax, groundY, F*0.36, F*0.06, 0, 0, Math.PI*2); ctx.fill();
      }
      // ground line
      if (detail === "high") {
        ctx.strokeStyle = `rgba(${base},0.18)`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(W*0.1, groundY); ctx.lineTo(W*0.9, groundY); ctx.stroke();
      }

      // treads
      const treadW = F * PROP.treadW;
      const treadH = F * PROP.treadH;
      const treadLeft = ax - treadW/2;
      ctx.fillStyle = "#000";
      roundRect(ctx, treadLeft, treadTop, treadW, treadH, treadH*0.45);
      ctx.fill();
      ctx.strokeStyle = `rgba(${base},0.95)`;
      ctx.lineWidth = swThick;
      if (detail === "high") { ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = 6; }
      ctx.stroke();
      ctx.shadowBlur = 0;
      const wheelR = treadH * 0.32;
      const wheelY = treadTop + treadH/2;
      const wheelGap = treadW * 0.22;
      const wheelXs = [treadLeft + wheelGap, ax, treadLeft + treadW - wheelGap];
      ctx.strokeStyle = `rgba(${base},0.7)`;
      ctx.lineWidth = sw*0.7;
      for (const wx of wheelXs) {
        ctx.beginPath(); ctx.arc(wx, wheelY, wheelR, 0, Math.PI*2); ctx.stroke();
        const ang = t * 5 + wx * 0.1 + (pose.treadPhase||0)*2;
        ctx.beginPath();
        ctx.moveTo(wx + Math.cos(ang)*wheelR*0.6, wheelY + Math.sin(ang)*wheelR*0.6);
        ctx.lineTo(wx - Math.cos(ang)*wheelR*0.6, wheelY - Math.sin(ang)*wheelR*0.6);
        ctx.moveTo(wx + Math.cos(ang+Math.PI/2)*wheelR*0.6, wheelY + Math.sin(ang+Math.PI/2)*wheelR*0.6);
        ctx.lineTo(wx - Math.cos(ang+Math.PI/2)*wheelR*0.6, wheelY - Math.sin(ang+Math.PI/2)*wheelR*0.6);
        ctx.stroke();
        ctx.fillStyle = `rgba(${base},0.95)`;
        ctx.beginPath(); ctx.arc(wx, wheelY, Math.max(1.4, sw*0.7), 0, Math.PI*2); ctx.fill();
      }
      if (detail !== "low") {
        const dashCount = 18;
        const trackOffset = ((t*30) + (pose.treadPhase||0)*40) % (treadW/dashCount);
        ctx.strokeStyle = `rgba(${base},0.4)`;
        ctx.lineWidth = 1.2;
        for (let i = -1; i < dashCount; i++) {
          const x = treadLeft + i*(treadW/dashCount) + trackOffset;
          if (x > treadLeft + 3 && x < treadLeft + treadW - 3) {
            ctx.beginPath(); ctx.moveTo(x, treadTop + 3); ctx.lineTo(x + 6, treadTop + 3); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(x, treadBot - 3); ctx.lineTo(x + 6, treadBot - 3); ctx.stroke();
          }
        }
      }

      // body
      const bodyTopY = bodyTop;
      const bodyTopL = bodyLeft - F*0.02;
      const bodyTopR = bodyLeft + bodyW + F*0.02;
      const bodyBotL = bodyLeft + F*0.02;
      const bodyBotR = bodyLeft + bodyW - F*0.02;
      const bevel = F*0.04;
      ctx.beginPath();
      ctx.moveTo(bodyTopL + bevel, bodyTopY);
      ctx.lineTo(bodyTopR - bevel, bodyTopY);
      ctx.quadraticCurveTo(bodyTopR, bodyTopY, bodyTopR, bodyTopY + bevel);
      ctx.lineTo(bodyBotR, bodyBottom - bevel);
      ctx.quadraticCurveTo(bodyBotR, bodyBottom, bodyBotR - bevel, bodyBottom);
      ctx.lineTo(bodyBotL + bevel, bodyBottom);
      ctx.quadraticCurveTo(bodyBotL, bodyBottom, bodyBotL, bodyBottom - bevel);
      ctx.lineTo(bodyTopL, bodyTopY + bevel);
      ctx.quadraticCurveTo(bodyTopL, bodyTopY, bodyTopL + bevel, bodyTopY);
      ctx.closePath();
      const bodyGrd = ctx.createLinearGradient(bodyTopL, 0, bodyTopR, 0);
      if (filled) {
        bodyGrd.addColorStop(0, `rgba(${base},0.18)`);
        bodyGrd.addColorStop(0.5, `rgba(${base},0.40)`);
        bodyGrd.addColorStop(1, `rgba(${base},0.18)`);
      } else {
        bodyGrd.addColorStop(0, "#000"); bodyGrd.addColorStop(0.4, "#050a05"); bodyGrd.addColorStop(1, "#000");
      }
      ctx.fillStyle = bodyGrd; ctx.fill();
      if (detail === "high") { ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = 8; }
      ctx.strokeStyle = `rgba(${base},0.95)`; ctx.lineWidth = swThick; ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = `rgba(${base},0.3)`; ctx.lineWidth = sw*0.6;
      ctx.beginPath(); ctx.moveTo(bodyTopL + F*0.04, bodyTopY + bodyH*0.18); ctx.lineTo(bodyTopR - F*0.04, bodyTopY + bodyH*0.18); ctx.stroke();

      if (detail !== "low") {
        const rivetR = Math.max(1.4, F*0.008);
        const rPad = bevel*0.9;
        const rivetPoints: [number, number][] = [[bodyTopL + rPad, bodyTopY + rPad],[bodyTopR - rPad, bodyTopY + rPad],[bodyBotL + rPad, bodyBottom - rPad],[bodyBotR - rPad, bodyBottom - rPad]];
        for (const [rx, ry] of rivetPoints) {
          if (detail === "high") { ctx.fillStyle = `rgba(${base},0.18)`; ctx.beginPath(); ctx.arc(rx, ry, rivetR*2, 0, Math.PI*2); ctx.fill(); }
          ctx.fillStyle = `rgba(${base},0.75)`; ctx.beginPath(); ctx.arc(rx, ry, rivetR, 0, Math.PI*2); ctx.fill();
        }
        ctx.strokeStyle = `rgba(${base},0.45)`; ctx.lineWidth = sw*0.55;
        for (let side = -1; side <= 1; side += 2) {
          const vxStart = side === -1 ? bodyTopL + F*0.025 : bodyTopR - F*0.025;
          const vyStart = bodyTopY + bodyH*0.58;
          for (let i = 0; i < 3; i++) {
            const vy = vyStart + i*bodyH*0.11;
            ctx.beginPath(); ctx.moveTo(vxStart, vy); ctx.lineTo(vxStart + side*F*0.045, vy); ctx.stroke();
          }
        }
        const decalX = bodyTopL + F*0.055;
        const decalY = bodyTopY + bodyH*0.085;
        ctx.fillStyle = `rgba(${base},0.55)`;
        ctx.font = `700 ${Math.max(7, F*0.026)}px JetBrains Mono, monospace`;
        ctx.textBaseline = "middle"; ctx.textAlign = "left";
        ctx.fillText("01", decalX, decalY);
        ctx.strokeStyle = `rgba(${base},0.40)`; ctx.lineWidth = sw*0.45;
        ctx.beginPath(); ctx.moveTo(decalX, decalY - F*0.022); ctx.lineTo(decalX + F*0.042, decalY - F*0.022); ctx.stroke();
        ctx.strokeStyle = `rgba(${base},0.18)`; ctx.lineWidth = sw*0.35;
        const scuffX = bodyTopR - F*0.08;
        const scuffY = bodyBottom - bodyH*0.18;
        for (let i = 0; i < 4; i++) {
          const off = i * F*0.006;
          ctx.beginPath(); ctx.moveTo(scuffX + off, scuffY); ctx.lineTo(scuffX + off + F*0.022, scuffY + F*0.022); ctx.stroke();
        }
      }

      // chest plate
      const chestW = bodyW * 0.55;
      const chestH = bodyH * 0.5;
      const chestX = ax - chestW/2;
      const chestY = bodyTopY + bodyH*0.30;
      ctx.fillStyle = "#020502";
      roundRect(ctx, chestX, chestY, chestW, chestH, chestH*0.18); ctx.fill();
      ctx.strokeStyle = `rgba(${base},0.7)`; ctx.lineWidth = sw*0.8; ctx.stroke();
      const chestGrd = ctx.createRadialGradient(ax, chestY + chestH/2, 0, ax, chestY + chestH/2, chestW*0.5);
      chestGrd.addColorStop(0, `rgba(${base},0.3)`); chestGrd.addColorStop(1, `rgba(${base},0)`);
      ctx.fillStyle = chestGrd; roundRect(ctx, chestX, chestY, chestW, chestH, chestH*0.18); ctx.fill();
      if (detail !== "low") {
        const ledY = chestY + chestH*0.5;
        const ledCount = 5;
        const ledGap = chestW / (ledCount + 1);
        for (let i = 0; i < ledCount; i++) {
          const lx = chestX + ledGap*(i+1);
          const phase = Math.sin(t*3 + i*0.7) > 0;
          let isActive = (currentState === "working") ? phase : (i === Math.floor(t*2) % ledCount);
          if (i === brokenLedIdx) {
            const flick = noise1(t*4.5, 17 + brokenLedIdx);
            if (flick < 0.55) isActive = false;
            else if (flick > 0.92) isActive = true;
          }
          ctx.fillStyle = isActive ? `rgb(${base})` : `rgba(${base},0.15)`;
          if (isActive && detail === "high") { ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = 8; }
          ctx.beginPath(); ctx.arc(lx, ledY, Math.max(1.4, F*0.008), 0, Math.PI*2); ctx.fill();
          ctx.shadowBlur = 0;
        }
        if (detail === "high") {
          ctx.fillStyle = `rgba(${base},0.6)`;
          ctx.font = `500 ${Math.max(7, F*0.022)}px JetBrains Mono, monospace`;
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillText("HMCL · 01", ax, chestY + chestH*0.22);
        }
      }

      // arms
      const shoulderY = bodyTopY + bodyH*0.10;
      const shoulderXL = bodyTopL;
      const shoulderXR = bodyTopR;
      const drawArm = (side: "L" | "R", mode: Pose["armL"]) => {
        const sx = side === "L" ? shoulderXL : shoulderXR;
        const sgn = side === "L" ? -1 : 1;
        const armLen = F * PROP.armLen;
        let elbowX = 0, elbowY = 0, handX = 0, handY = 0;
        const swArm = sw;
        switch (mode) {
          case "rest": default: elbowX = sx + sgn*armLen*0.18; elbowY = shoulderY + armLen*0.55; handX = sx + sgn*armLen*0.08; handY = shoulderY + armLen*0.95; break;
          case "up": elbowX = sx + sgn*armLen*0.10; elbowY = shoulderY - armLen*0.35; handX = sx + sgn*armLen*0.25; handY = shoulderY - armLen*0.80; break;
          case "chin": elbowX = sx + sgn*armLen*0.40; elbowY = shoulderY + armLen*0.10; handX = ax + sgn*F*0.02; handY = headPivotY - headH*0.30; break;
          case "gesture": {
            const gest = (Math.sin(performance.now()*0.004) + 1)/2;
            elbowX = sx + sgn*armLen*0.30; elbowY = shoulderY + armLen*0.15 - gest*armLen*0.10;
            handX = sx + sgn*armLen*0.55 - sgn*gest*armLen*0.05; handY = shoulderY - armLen*0.15 - gest*armLen*0.15; break;
          }
          case "work": {
            const typing = Math.sin(performance.now()*0.012 + (side==="L"?0:Math.PI))*0.5 + 0.5;
            elbowX = sx + sgn*armLen*0.10; elbowY = shoulderY + armLen*0.45;
            handX = sx + sgn*armLen*0.05 - sgn*F*0.04; handY = shoulderY + armLen*0.85 - typing*armLen*0.04; break;
          }
        }
        ctx.beginPath(); ctx.moveTo(sx, shoulderY); ctx.lineTo(elbowX, elbowY);
        ctx.strokeStyle = `rgba(${base},0.95)`; ctx.lineWidth = swArm*1.3; ctx.lineCap = "round"; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(elbowX, elbowY); ctx.lineTo(handX, handY); ctx.stroke();
        ctx.fillStyle = `rgba(${base},1)`; ctx.beginPath(); ctx.arc(sx, shoulderY, swArm*1.2, 0, Math.PI*2); ctx.fill();
        ctx.fillStyle = `rgba(${base},0.85)`; ctx.beginPath(); ctx.arc(elbowX, elbowY, swArm*0.9, 0, Math.PI*2); ctx.fill();
        const handR = swArm * 1.6;
        const handAng = Math.atan2(handY - elbowY, handX - elbowX);
        ctx.save(); ctx.translate(handX, handY); ctx.rotate(handAng);
        ctx.fillStyle = `rgba(${base},1)`; ctx.beginPath(); ctx.arc(0, 0, handR*0.8, 0, Math.PI*2); ctx.fill();
        ctx.strokeStyle = `rgba(${base},0.95)`; ctx.lineWidth = swArm*0.9;
        ctx.beginPath(); ctx.moveTo(0, -handR*0.3); ctx.lineTo(handR*1.0, -handR*0.5);
        ctx.moveTo(0, handR*0.3); ctx.lineTo(handR*1.0, handR*0.5); ctx.stroke();
        ctx.restore();
      };
      drawArm("L", pose.armL);
      drawArm("R", pose.armR);

      // neck
      ctx.fillStyle = "#000"; roundRect(ctx, neckLeft, neckTop, neckW, neckH, neckH*0.4); ctx.fill();
      ctx.strokeStyle = `rgba(${base},0.85)`; ctx.lineWidth = sw*0.9; ctx.stroke();
      ctx.beginPath(); ctx.moveTo(neckLeft + neckW*0.2, neckTop + neckH*0.5); ctx.lineTo(neckLeft + neckW*0.8, neckTop + neckH*0.5);
      ctx.strokeStyle = `rgba(${base},0.4)`; ctx.lineWidth = sw*0.5; ctx.stroke();

      // head
      ctx.save();
      ctx.translate(headPivotX + headPan, headPivotY + bobY - lift);
      ctx.rotate(headTilt);
      const hx = -headW/2;
      const hy = -headH;
      const hr = headH * 0.25;
      ctx.fillStyle = filled ? `rgba(${base},0.32)` : "#050a05";
      roundRect(ctx, hx, hy, headW, headH, hr); ctx.fill();
      if (detail === "high") { ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = 12 * (pose.antennaPulse || 0.5); }
      ctx.strokeStyle = `rgba(${base},0.95)`; ctx.lineWidth = swThick; ctx.stroke();
      ctx.shadowBlur = 0;
      if (detail !== "low") {
        const grd = ctx.createLinearGradient(hx, hy, hx, hy + headH);
        grd.addColorStop(0, `rgba(${base},0.18)`); grd.addColorStop(0.5, `rgba(${base},0.04)`); grd.addColorStop(1, `rgba(${base},0)`);
        ctx.fillStyle = grd; roundRect(ctx, hx, hy, headW, headH, hr); ctx.fill();
      }

      // eyes
      const eyeR = headH * 0.42;
      const eyeY = -headH * 0.55;
      const eyeGap = headW * 0.27;
      const eyeLX = -eyeGap;
      const eyeRX = eyeGap;
      const drawEyeBarrel = (cxE: number, cyE: number) => {
        ctx.fillStyle = "#000"; ctx.beginPath(); ctx.arc(cxE, cyE, eyeR * 1.18, 0, Math.PI*2); ctx.fill();
        ctx.strokeStyle = `rgba(${base},0.95)`; ctx.lineWidth = sw*1.6;
        ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = detail === "high" ? 6 : 2; ctx.stroke();
        ctx.shadowBlur = 0;
        ctx.strokeStyle = `rgba(${base},0.45)`; ctx.lineWidth = sw*0.6;
        ctx.beginPath(); ctx.arc(cxE, cyE, eyeR * 1.04, 0, Math.PI*2); ctx.stroke();
      };
      drawEyeBarrel(eyeLX, eyeY);
      drawEyeBarrel(eyeRX, eyeY);
      ctx.strokeStyle = `rgba(${base},0.95)`; ctx.lineWidth = sw*1.1;
      ctx.beginPath(); ctx.moveTo(eyeLX + eyeR*1.10, eyeY); ctx.lineTo(eyeRX - eyeR*1.10, eyeY); ctx.stroke();

      const lookOffX = pose.eyeLook[0] * eyeR * 0.35;
      const lookOffY = pose.eyeLook[1] * eyeR * 0.35;
      const dilation = pose.eyeDilation;
      const irisR = eyeR * 0.5 * dilation;
      const pupilR = irisR * 0.55;

      const drawLens = (cxE: number, cyE: number, h: number) => {
        const cavityR = eyeR * 0.92;
        const cavGrd = ctx.createRadialGradient(cxE - eyeR*0.2, cyE - eyeR*0.2, 0, cxE, cyE, cavityR);
        cavGrd.addColorStop(0, `rgba(${base},0.12)`); cavGrd.addColorStop(0.7, "rgba(0,0,0,0.95)"); cavGrd.addColorStop(1, "rgba(0,0,0,1)");
        ctx.fillStyle = cavGrd; ctx.beginPath(); ctx.arc(cxE, cyE, cavityR, 0, Math.PI*2); ctx.fill();

        if (pose.eyeShape === "x") {
          ctx.strokeStyle = `rgba(${hot},0.95)`; ctx.lineWidth = sw*1.6; ctx.lineCap = "round";
          ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = detail === "high" ? 8 : 2;
          const xR = eyeR * 0.55;
          ctx.beginPath();
          ctx.moveTo(cxE - xR, cyE - xR); ctx.lineTo(cxE + xR, cyE + xR);
          ctx.moveTo(cxE + xR, cyE - xR); ctx.lineTo(cxE - xR, cyE + xR);
          ctx.stroke(); ctx.shadowBlur = 0;
          return;
        }
        if (pose.eyeShape === "crescent") {
          ctx.strokeStyle = `rgba(${hot},1)`; ctx.lineWidth = sw*2.2; ctx.lineCap = "round";
          ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = detail === "high" ? 8 : 2;
          ctx.beginPath(); ctx.arc(cxE, cyE + eyeR*0.2, eyeR*0.55, Math.PI*1.15, Math.PI*1.85); ctx.stroke();
          ctx.shadowBlur = 0; return;
        }
        if (detail !== "low" && h > 0.3) {
          ctx.strokeStyle = `rgba(${base},0.22)`; ctx.lineWidth = sw*0.4;
          ctx.beginPath(); ctx.arc(cxE, cyE, eyeR*0.78, 0, Math.PI*2); ctx.stroke();
          ctx.strokeStyle = `rgba(${base},0.14)`;
          ctx.beginPath(); ctx.arc(cxE, cyE, eyeR*0.62, 0, Math.PI*2); ctx.stroke();
        }
        ctx.fillStyle = `rgba(${base},${0.4 + dilation*0.3})`;
        ctx.beginPath(); ctx.ellipse(cxE + lookOffX, cyE + lookOffY, irisR, irisR * h, 0, 0, Math.PI*2); ctx.fill();
        ctx.fillStyle = `rgba(${hot},0.95)`;
        ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = detail === "high" ? 10 * dilation : 3;
        ctx.beginPath(); ctx.ellipse(cxE + lookOffX, cyE + lookOffY, pupilR, pupilR * h, 0, 0, Math.PI*2); ctx.fill();
        ctx.shadowBlur = 0;
        ctx.fillStyle = "#fff";
        ctx.beginPath(); ctx.ellipse(cxE + lookOffX - pupilR*0.2, cyE + lookOffY - pupilR*0.2, pupilR*0.22, pupilR*0.22 * h, 0, 0, Math.PI*2); ctx.fill();
        if (h < 0.2) {
          ctx.strokeStyle = `rgba(${base},0.9)`; ctx.lineWidth = sw * 1.3; ctx.lineCap = "round";
          ctx.beginPath(); ctx.moveTo(cxE - eyeR*0.6, cyE); ctx.lineTo(cxE + eyeR*0.6, cyE); ctx.stroke();
        }
      };
      drawLens(eyeLX, eyeY, eyeOpenLSmoothed);
      drawLens(eyeRX, eyeY, eyeOpenRSmoothed);

      if (detail !== "low" && pose.eyeShape === "circle") {
        for (const cxE of [eyeLX, eyeRX]) {
          const reflGrd = ctx.createRadialGradient(cxE - eyeR*0.45, eyeY - eyeR*0.45, 0, cxE - eyeR*0.45, eyeY - eyeR*0.45, eyeR*0.45);
          reflGrd.addColorStop(0, "rgba(255,255,255,0.28)"); reflGrd.addColorStop(1, "rgba(255,255,255,0)");
          ctx.fillStyle = reflGrd; ctx.beginPath(); ctx.arc(cxE - eyeR*0.45, eyeY - eyeR*0.45, eyeR*0.45, 0, Math.PI*2); ctx.fill();
          ctx.fillStyle = "rgba(255,255,255,0.75)";
          ctx.beginPath(); ctx.ellipse(cxE - eyeR*0.48, eyeY - eyeR*0.48, eyeR*0.10, eyeR*0.07, 0, 0, Math.PI*2); ctx.fill();
          ctx.fillStyle = "rgba(255,255,255,0.32)";
          ctx.beginPath(); ctx.ellipse(cxE - eyeR*0.20, eyeY + eyeR*0.38, eyeR*0.07, eyeR*0.045, 0, 0, Math.PI*2); ctx.fill();
        }
      }
      if (detail !== "low") {
        ctx.strokeStyle = `rgba(${base},0.5)`; ctx.lineWidth = sw*0.6;
        for (let i = 0; i < 3; i++) {
          const y = -headH*0.6 + i*headH*0.18;
          ctx.beginPath(); ctx.moveTo(-headW/2 - sw, y); ctx.lineTo(-headW/2 - sw*4, y);
          ctx.moveTo(headW/2 + sw, y); ctx.lineTo(headW/2 + sw*4, y); ctx.stroke();
        }
      }

      // antenna
      const antennaH = F * PROP.antenna;
      const antennaPulse = pose.antennaPulse;
      const swayX = Math.sin(t*1.3) * antennaH * 0.06;
      ctx.fillStyle = `rgba(${base},0.95)`;
      ctx.beginPath(); ctx.arc(0, -headH, sw*1.2, 0, Math.PI*2); ctx.fill();
      if (detail !== "low") {
        ctx.strokeStyle = `rgba(${base},0.45)`; ctx.lineWidth = sw*0.45;
        ctx.beginPath(); ctx.arc(0, -headH, sw*2.3, 0, Math.PI*2); ctx.stroke();
      }
      ctx.strokeStyle = `rgba(${base},0.9)`; ctx.lineWidth = sw*0.85; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(0, -headH); ctx.quadraticCurveTo(swayX*0.5, -headH - antennaH*0.55, swayX, -headH - antennaH); ctx.stroke();
      if (detail !== "low") {
        ctx.fillStyle = `rgba(${base},0.75)`; ctx.beginPath(); ctx.arc(swayX*0.5, -headH - antennaH*0.5, sw*0.5, 0, Math.PI*2); ctx.fill();
      }
      const tipR = Math.max(2.5, F*0.018);
      const tipX = swayX, tipY = -headH - antennaH;
      if (detail !== "low") {
        ctx.strokeStyle = `rgba(${hot},${0.3 + antennaPulse*0.4})`; ctx.lineWidth = sw*0.45;
        ctx.beginPath(); ctx.arc(tipX, tipY, tipR*2, 0, Math.PI*2); ctx.stroke();
      }
      const tipFlicker = 0.65 + (Math.sin(t*4)*0.5+0.5)*0.35 * antennaPulse;
      ctx.fillStyle = `rgba(${hot},${tipFlicker})`;
      if (detail === "high") { ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = 18 * antennaPulse; }
      ctx.beginPath(); ctx.arc(tipX, tipY, tipR, 0, Math.PI*2); ctx.fill();
      ctx.shadowBlur = 0;
      if (detail !== "low") {
        ctx.fillStyle = `rgba(255,255,255,${0.55 + antennaPulse*0.35})`;
        ctx.beginPath(); ctx.arc(tipX, tipY, tipR*0.32, 0, Math.PI*2); ctx.fill();
      }

      // listening waves
      if (pose.showEars && detail !== "low") {
        for (let side = -1; side <= 1; side += 2) {
          for (let i = 0; i < 3; i++) {
            const phase = ((t*1.6) + i*0.4) % 1.6;
            if (phase < 1.4) {
              const r = (1 - phase/1.4) * headW*0.06 + headW*0.04;
              const cxE = side * (headW*0.55 + headW*0.08 * (1-phase/1.4));
              ctx.beginPath();
              ctx.arc(cxE, -headH*0.55, r, side === -1 ? -Math.PI*0.4 : Math.PI*0.6, side === -1 ? Math.PI*0.4 : Math.PI*1.4);
              ctx.strokeStyle = `rgba(${base},${(1-phase/1.4)*0.7})`; ctx.lineWidth = 1.5; ctx.stroke();
            }
          }
        }
      }
      // speaker grille
      if (detail !== "low") {
        const grilleW = headW * 0.36;
        const grilleH = headH * 0.14;
        const grilleX = -grilleW/2;
        const grilleY = -headH*0.16;
        ctx.fillStyle = "#020502";
        roundRect(ctx, grilleX, grilleY - grilleH/2, grilleW, grilleH, grilleH*0.3); ctx.fill();
        ctx.strokeStyle = `rgba(${base},0.55)`; ctx.lineWidth = sw*0.45; ctx.stroke();
        const speak = pose.speakIntensity ?? 0;
        const responding = pose.showSpeech;
        const slits = 4;
        for (let i = 0; i < slits; i++) {
          const sy = (grilleY - grilleH/2) + grilleH*(i+1)/(slits+1) - 0.5;
          let lvl: number;
          if (responding) lvl = 0.45 + (Math.sin(t*9 + i*0.8)*0.5+0.5) * (speak + 0.35);
          else lvl = 0.16 + Math.sin(t*0.6 + i*0.4)*0.04;
          ctx.fillStyle = `rgba(${hot},${Math.min(1, lvl)})`;
          if (responding && detail === "high") { ctx.shadowColor = `rgb(${base})`; ctx.shadowBlur = 3 + lvl*5; }
          ctx.fillRect(grilleX + grilleW*0.06, sy, grilleW*0.88, Math.max(1.2, sw*0.7));
          ctx.shadowBlur = 0;
        }
      }
      // error warn
      if (pose.showWarn) {
        const tx = 0; const ty = -headH - antennaH - F*0.08; const ts = F*0.05;
        const flash = Math.sin(t*6) > 0 ? 1 : 0.4;
        ctx.strokeStyle = `rgba(${base},${flash})`; ctx.lineWidth = 1.8;
        ctx.beginPath();
        ctx.moveTo(tx, ty - ts); ctx.lineTo(tx + ts, ty + ts*0.7); ctx.lineTo(tx - ts, ty + ts*0.7);
        ctx.closePath(); ctx.stroke();
        ctx.fillStyle = `rgba(${base},${flash})`;
        ctx.font = `700 ${Math.max(7, F*0.04)}px JetBrains Mono, monospace`;
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText("!", tx, ty + ts*0.15);
      }
      ctx.restore(); // head

      // thinking bubble
      if (pose.showThought && detail !== "low") {
        const tx = ax + headW*0.6;
        const ty = headPivotY - headH * 1.5 + bobY;
        ctx.fillStyle = `rgba(${base},0.55)`;
        ctx.beginPath(); ctx.arc(ax + headW*0.4, headPivotY - headH*1.0, 2.5, 0, Math.PI*2); ctx.fill();
        ctx.beginPath(); ctx.arc(ax + headW*0.5, headPivotY - headH*1.25, 3.5, 0, Math.PI*2); ctx.fill();
        const bubR = F*0.05;
        ctx.beginPath(); ctx.arc(tx, ty, bubR, 0, Math.PI*2);
        ctx.fillStyle = "rgba(0,0,0,0.9)"; ctx.fill();
        ctx.strokeStyle = `rgba(${base},0.8)`; ctx.lineWidth = 1.4; ctx.stroke();
        const glyphs = "?!#*@~&%";
        const g = glyphs[Math.floor(t*2.5) % glyphs.length];
        ctx.fillStyle = `rgba(${hot},0.95)`;
        ctx.font = `500 ${Math.floor(F*0.05)}px JetBrains Mono, monospace`;
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(g, tx, ty + 1);
      }

      // working: device
      if (pose.showCode && detail !== "low") {
        const devW = bodyW * 0.7;
        const devH = bodyH * 0.45;
        const devX = ax - devW/2;
        const devY = bodyTopY + bodyH*0.55;
        ctx.fillStyle = "#020502";
        roundRect(ctx, devX, devY, devW, devH, devH*0.12); ctx.fill();
        ctx.strokeStyle = `rgba(${base},0.8)`; ctx.lineWidth = sw*0.7; ctx.stroke();
        const screenGrd = ctx.createRadialGradient(ax, devY + devH/2, 0, ax, devY + devH/2, devW*0.4);
        screenGrd.addColorStop(0, `rgba(${hot},0.3)`); screenGrd.addColorStop(1, `rgba(${base},0)`);
        ctx.fillStyle = screenGrd; roundRect(ctx, devX, devY, devW, devH, devH*0.12); ctx.fill();
        ctx.fillStyle = `rgba(${hot},0.9)`;
        const ll = Math.max(2, devH*0.08);
        for (let i = 0; i < 4; i++) {
          const w = devW * 0.4 * (0.4 + ((i + Math.floor(t*2.5)) % 5)/5 * 0.55);
          ctx.fillRect(devX + devW*0.08, devY + devH*0.18 + i*ll*1.7, w, ll*0.7);
        }
        if (Math.sin(t*8) > 0) ctx.fillRect(devX + devW*0.08, devY + devH*0.18 + 4*ll*1.7, ll*0.7, ll*0.7);
      }

      // sparks
      for (let i = sparks.length - 1; i >= 0; i--) {
        const sp = sparks[i];
        if (sp.life >= sp.max) { sparks.splice(i, 1); continue; }
        sp.life++; sp.x += sp.vx; sp.y += sp.vy; sp.vy += 0.12;
        const a = 1 - sp.life/sp.max;
        ctx.fillStyle = `rgba(${hot},${a})`;
        ctx.fillRect(sp.x - sp.size/2, sp.y - sp.size/2, sp.size, sp.size);
      }
      if (pose.showSparkErr && detail !== "low" && Math.random() < 0.2) {
        ctx.fillStyle = `rgba(${hot},0.95)`;
        for (let i = 0; i < 3; i++) {
          const sx = headPivotX + (Math.random()-0.5)*headW;
          const sy = headPivotY - headH * (0.5 + Math.random()*0.5) + bobY;
          ctx.fillRect(sx, sy, 1.5, 1.5);
        }
      }

      // ─── 90s SIGNATURE GLITCH ────────────────────────────────
      // Every ~80-110s the robot tears horizontally for ~280ms — a
      // CRT vertical-hold pop. One event per minute-and-a-half is
      // restraint; ten would be noise. Only fires in non-error
      // non-boot states so we don't step on a real signal.
      if (currentState !== "boot" && currentState !== "error") {
        if (t > nextGlitchT && glitchActive < 0) {
          glitchActive = t;
          nextGlitchT = t + 80 + Math.random() * 30;
        }
      }
      if (glitchActive > 0) {
        const dur = t - glitchActive;
        if (dur > 0.28) {
          glitchActive = -1;
        } else {
          const intensity = Math.sin((dur / 0.28) * Math.PI);
          const slice = Math.floor(H * (0.25 + Math.random() * 0.45));
          const sliceH = Math.max(2, Math.floor(H * (0.04 + Math.random() * 0.08)));
          // Tear: copy a horizontal band and re-paste shifted.
          try {
            const data = ctx.getImageData(0, slice, W, sliceH);
            const shift = Math.floor((Math.random() - 0.5) * W * 0.18 * intensity);
            ctx.putImageData(data, shift, slice);
          } catch { /* tainted canvas — silently skip */ }
          // Scanline flash across the whole canvas.
          ctx.fillStyle = `rgba(${hot},${0.06 * intensity})`;
          ctx.fillRect(0, 0, W, H);
          // Hard-edge sliver line at the tear.
          ctx.fillStyle = `rgba(${hot},${0.4 * intensity})`;
          ctx.fillRect(0, slice, W, 1);
          ctx.fillRect(0, slice + sliceH, W, 1);
        }
      }

      raf = requestAnimationFrame(render);
    };
    let nextGlitchT = 25 + Math.random() * 30; // first glitch after 25-55s
    let glitchActive = -1;
    raf = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, [detail, noDust, filled, palette]);

  return <canvas ref={canvasRef} className={className} style={style} />;
}
