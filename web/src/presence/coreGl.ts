// The presence, drawn on the GPU: a shell of soft points that turns while it works.
// Ported from the design mock (homunculus-design/console-v13/shared/core-gl.js); the
// shaders are unchanged. Every signal it shows comes from the console's state: a ripple
// per tool step, amber while it waits for the operator, a green flare when a job is
// done, a sag when it is stuck.

export type CoreMode = "idle" | "work" | "think" | "wait" | "stuck" | "listen" | "speak" | "rewind";
export type PulseTone = "live" | "bad" | "attn" | "ok" | "cool";

export interface Pulse {
  /** Direction on the sphere the ripple starts from, unit length. */
  o: [number, number, number];
  /** Seconds, on the same clock as `CoreModel.t`. */
  t0: number;
  tone: PulseTone;
}

/** One frame of the presence. */
export interface CoreModel {
  w: number;
  h: number;
  dpr: number;
  t: number;
  dt: number;
  mode: CoreMode;
  pulses: Pulse[];
  flare: number;
  amp: number;
  p: number;
  born: number;
  lean: [number, number, number];
  leanAmt: number;
  reduce: boolean;
}

export interface Core {
  draw(m: CoreModel): void;
}

const VS = `
precision highp float;
attribute vec3 aDir; attribute vec4 aSeed;
uniform float uTime, uYaw, uTilt, uBreath, uNoise, uFlow, uFlare, uAmp, uProg, uScale, uAspect, uDpr, uSize;
uniform float uWait, uStuck, uVoice, uIdle, uBorn;
uniform vec4 uPulse[6]; uniform float uBad[6]; uniform vec3 uLean; uniform float uLeanAmt;
uniform vec3 cLo, cMid, cHi, cLive, cAttn, cErr, cOk;
varying vec3 vCol; varying float vA;
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy; i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx; vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_); vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw); vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3))); p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
vec3 yaw(vec3 p,float a){float c=cos(a),s=sin(a);return vec3(p.x*c+p.z*s,p.y,-p.x*s+p.z*c);}
vec3 tilt(vec3 p,float a){float c=cos(a),s=sin(a);return vec3(p.x,p.y*c-p.z*s,p.y*s+p.z*c);}
void main(){
  float type=aSeed.w; vec3 pos; vec3 col; float a; float size; float lit=0.0; float bad=0.0;
  if(type<0.5){
    vec3 d=aDir; vec3 q=tilt(yaw(d,uYaw),uTilt);
    float n=snoise(d*1.6+vec3(0.0,uTime*uFlow,uTime*uFlow*0.6));
    float r=1.0+(aSeed.x-0.5)*0.07+uBreath+n*uNoise;
    for(int i=0;i<6;i++){ vec4 P=uPulse[i]; if(P.w>0.0&&P.w<2.4){ float ang=acos(clamp(dot(q,P.xyz),-1.0,1.0)); float fr=P.w*2.1; float w=exp(-pow(ang-fr,2.0)/0.02)*exp(-P.w*1.25); if(uBad[i]>0.5&&uBad[i]<1.5) w=0.0; r+=w*0.035; if(w>lit){lit=w;bad=uBad[i];} } }
    // its body leans toward the work it is sending, and faces the ask while it waits
    float face=dot(q,uLean);
    r+=uAmp*uVoice*0.08*sin(d.y*9.0+uTime*6.0+aSeed.y*6.0);
    // stuck: one side dented and still
    float dent=max(0.0,dot(q,normalize(vec3(0.62,-0.48,0.62)))); r-=uStuck*0.6*dent*dent;
    float shed=step(aSeed.y,0.16)*step(0.3,dent); r+=uStuck*shed*fract(uTime*0.14+aSeed.x)*1.3;
    if(d.y<-0.1) r-=(-0.1-d.y)*0.22*uStuck;
    r+=uFlare*0.26*sin(uFlare*3.14159);
    float b=1.0-pow(1.0-clamp(uBorn*1.15-aSeed.y*0.15,0.0,1.0),3.0); r*=mix(1.18+aSeed.z*0.17,1.0,b); pos=q*r;
    float lum=max(0.0,dot(q,normalize(vec3(-0.42,0.52,0.74))));
    float front=(q.z+1.0)*0.5;
    col=mix(cLo,cMid,smoothstep(0.08,0.42,lum)); col=mix(col,cHi,smoothstep(0.55,0.92,lum)); col=mix(col,cHi,uLeanAmt*0.5*smoothstep(0.4,0.95,face));
    if(aSeed.z<uWait*(0.25+0.75*smoothstep(-0.2,0.6,face))&&front>0.25) col=mix(col,cAttn,0.92);
    if(aSeed.z<uStuck*0.2) col=cErr;
    if(aSeed.z<uFlare) col=mix(col,cOk,0.9);
    if(aSeed.z<(0.2+uAmp*0.5)*uVoice&&front>0.45) col=mix(col,cLive,0.85);
    col=mix(col,cHi,clamp(lit*1.1,0.0,0.85));
    a=((0.12+0.88*lum)*(0.3+0.7*front)*mix(1.0,0.85,uIdle)*mix(1.0,0.6,uStuck)+lit*0.7)*b*b;
    a*=mix(1.0,0.5+0.9*smoothstep(-0.3,0.85,face),uLeanAmt); a=min(a,0.82);
    if(aSeed.z>1.0-0.3*uIdle) a*=0.2;
    if(shed>0.5) a*=uStuck*(1.0-fract(uTime*0.14+aSeed.x))*1.4;
    size=(1.4+2.3*front+lit*2.0)*uSize;
  } else if(type>2.5){
    // the inner light: one wide soft point behind the shell, in the colour of its state
    vec3 sc=uWait>0.5?cAttn:uStuck>0.5?cErr:uIdle>0.5?cMid:cLive;
    pos=vec3(0.0,0.0,-0.2); col=mix(cMid,sc,0.75); a=(0.10+0.05*sin(uTime*1.3)+uFlare*0.25+uAmp*uVoice*0.12)*uBorn; size=170.0*uSize;
  } else { pos=vec3(0.0); col=vec3(0.0); a=0.0; size=0.0; }
  float s=3.4/(3.4-pos.z);
  gl_Position=vec4(pos.x*s*uScale/uAspect,pos.y*s*uScale,0.0,1.0);
  gl_PointSize=size*uDpr*s;
  vCol=col; vA=a;
}`;
const FS = `
precision mediump float; varying vec3 vCol; varying float vA;
void main(){ vec2 c=gl_PointCoord-0.5; float d=length(c); float a=smoothstep(0.5,0.05,d); a*=a; float k=a*vA*0.95; gl_FragColor=vec4(vCol*k,k); }`;

const UNIFORMS = ["uTime", "uYaw", "uTilt", "uBreath", "uNoise", "uFlow", "uFlare", "uAmp", "uProg", "uScale", "uAspect", "uDpr", "uSize", "uWait", "uStuck", "uVoice", "uIdle", "uBorn", "uLean", "uLeanAmt", "uPulse", "uBad", "cLo", "cMid", "cHi", "cLive", "cAttn", "cErr", "cOk"] as const;
// Theme colours come from the CSS tokens, resolved to sRGB by the browser.
const COLOURS: Record<string, string> = { cLo: "--point-lo", cMid: "--point-mid", cHi: "--point-hi", cLive: "--color-accent", cAttn: "--color-attn", cErr: "--color-error", cOk: "--color-success" };
const SPIN: Record<CoreMode, number> = { work: 0.38, think: 1.0, listen: 0.22, speak: 0.28, wait: 0.06, stuck: 0.0, idle: 0.06, rewind: -0.5 };
const TONE: Record<PulseTone, number> = { live: 0, bad: 1, attn: 2, ok: 3, cool: 4 };

/** Set up the presence on a canvas, or return null where WebGL is not available. */
export function initCore(canvas: HTMLCanvasElement): Core | null {
  let gl: WebGLRenderingContext | null = null;
  try {
    gl = canvas.getContext("webgl", { premultipliedAlpha: true, antialias: true, alpha: true });
  } catch {
    gl = null;
  }
  if (!gl) return null;
  const g = gl;
  const shader = (type: number, source: string) => {
    const s = g.createShader(type);
    if (!s) return null;
    g.shaderSource(s, source);
    g.compileShader(s);
    if (!g.getShaderParameter(s, g.COMPILE_STATUS)) {
      console.warn(g.getShaderInfoLog(s));
      return null;
    }
    return s;
  };
  const vs = shader(g.VERTEX_SHADER, VS);
  const fs = shader(g.FRAGMENT_SHADER, FS);
  const program = g.createProgram();
  if (!vs || !fs || !program) return null;
  g.attachShader(program, vs);
  g.attachShader(program, fs);
  g.linkProgram(program);
  if (!g.getProgramParameter(program, g.LINK_STATUS)) {
    console.warn(g.getProgramInfoLog(program));
    return null;
  }
  g.useProgram(program);

  // The points: a shell on a golden-angle spiral, then one wide inner light.
  let seed = 11;
  const rnd = () => {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  };
  const N = 4200;
  const GOLDEN = Math.PI * (3 - Math.sqrt(5));
  const dir: number[] = [];
  const seeds: number[] = [];
  for (let i = 0; i < N; i++) {
    const y = 1 - (2 * (i + 0.5)) / N;
    const r = Math.sqrt(1 - y * y);
    const th = i * GOLDEN + (rnd() - 0.5) * 0.4;
    dir.push(Math.cos(th) * r, y, Math.sin(th) * r);
    seeds.push(rnd(), rnd(), rnd(), 0);
  }
  dir.push(0, 0, 0);
  seeds.push(0, 0, 0, 3);
  const attribute = (data: number[], name: string, size: number) => {
    g.bindBuffer(g.ARRAY_BUFFER, g.createBuffer());
    g.bufferData(g.ARRAY_BUFFER, new Float32Array(data), g.STATIC_DRAW);
    const loc = g.getAttribLocation(program, name);
    g.enableVertexAttribArray(loc);
    g.vertexAttribPointer(loc, size, g.FLOAT, false, 0, 0);
  };
  attribute(dir, "aDir", 3);
  attribute(seeds, "aSeed", 4);
  const COUNT = N + 1;

  const U = Object.fromEntries(UNIFORMS.map((k) => [k, g.getUniformLocation(program, k)])) as Record<(typeof UNIFORMS)[number], WebGLUniformLocation | null>;
  const probe = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  const rgb = (token: string): [number, number, number] => {
    if (!probe) return [1, 1, 1];
    probe.canvas.width = probe.canvas.height = 1;
    probe.clearRect(0, 0, 1, 1);
    probe.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
    probe.fillRect(0, 0, 1, 1);
    const d = probe.getImageData(0, 0, 1, 1).data;
    return [d[0] / 255, d[1] / 255, d[2] / 255];
  };
  Object.entries(COLOURS).forEach(([u, token]) => g.uniform3fv(U[u as keyof typeof U], rgb(token)));
  g.enable(g.BLEND);
  g.blendFunc(g.ONE, g.ONE);

  let yaw = 0;
  // Smoothed state, so moving between states is a blend and never a cut.
  const sm = { lean: 0, breath: 0, noise: 0.04, flow: 0.1, wait: 0, stuck: 0, voice: 0, idle: 0 };
  const ease = (k: keyof typeof sm, target: number, dt: number, rate: number) => (sm[k] += (target - sm[k]) * Math.min(1, dt * rate));

  return {
    draw(m) {
      const w = Math.round(m.w * m.dpr);
      const h = Math.round(m.h * m.dpr);
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
      g.viewport(0, 0, w, h);
      g.clearColor(0, 0, 0, 0);
      g.clear(g.COLOR_BUFFER_BIT);
      const md = m.mode;
      const t = m.t;
      const dt = m.reduce ? 1 : m.dt;
      const R = Math.min(m.w, m.h) * 0.36;
      yaw += m.dt * (SPIN[md] ?? 0.1);
      const breath: Partial<Record<CoreMode, number>> = { idle: 0.018 * Math.sin(t * 0.55), work: 0.012 * Math.sin(t * 2.3), think: -0.09, wait: -0.08 + 0.03 * Math.max(0, Math.sin(t * 2.2)) ** 6, stuck: -0.03, listen: 0.03, speak: 0.04 };
      const noise: Partial<Record<CoreMode, number>> = { idle: 0.035, work: 0.06, think: 0.11, wait: 0.04, stuck: 0.02, listen: 0.07, speak: 0.08 };
      const flow: Partial<Record<CoreMode, number>> = { idle: 0.08, work: 0.22, think: 0.6, wait: 0.1, stuck: 0.02, listen: 0.3, speak: 0.35 };
      ease("breath", breath[md] ?? 0, dt, 6);
      ease("noise", noise[md] ?? 0.04, dt, 3);
      ease("flow", flow[md] ?? 0.1, dt, 3);
      ease("wait", md === "wait" ? 1 : 0, dt, 3);
      ease("stuck", md === "stuck" ? 1 : 0, dt, 3);
      ease("voice", md === "listen" || md === "speak" ? 1 : 0, dt, 5);
      ease("idle", md === "idle" ? 1 : 0, dt, 2);
      g.uniform1f(U.uTime, t);
      g.uniform1f(U.uYaw, yaw);
      g.uniform1f(U.uTilt, 0.36);
      const calm = Math.min(1, R / 72);
      g.uniform1f(U.uBreath, sm.breath * calm);
      g.uniform1f(U.uNoise, sm.noise * calm * calm);
      g.uniform1f(U.uFlow, sm.flow);
      g.uniform1f(U.uFlare, m.flare);
      g.uniform1f(U.uAmp, m.amp);
      g.uniform1f(U.uProg, m.p);
      g.uniform1f(U.uScale, R / (m.h / 2));
      g.uniform1f(U.uAspect, m.w / m.h);
      g.uniform1f(U.uDpr, m.dpr);
      g.uniform1f(U.uSize, Math.max(0.7, Math.min(1.3, R / 72)));
      g.uniform1f(U.uWait, sm.wait);
      g.uniform1f(U.uStuck, sm.stuck);
      g.uniform1f(U.uVoice, sm.voice);
      g.uniform1f(U.uIdle, sm.idle);
      g.uniform1f(U.uBorn, m.born);
      const P = new Float32Array(24);
      const B = new Float32Array(6);
      m.pulses.slice(-6).forEach((p, i) => {
        P.set([p.o[0], p.o[1], p.o[2], t - p.t0], i * 4);
        B[i] = TONE[p.tone];
      });
      g.uniform4fv(U.uPulse, P);
      g.uniform1fv(U.uBad, B);
      g.uniform3fv(U.uLean, m.lean);
      ease("lean", m.leanAmt, dt, 3);
      g.uniform1f(U.uLeanAmt, sm.lean);
      g.drawArrays(g.POINTS, 0, COUNT);
    },
  };
}
