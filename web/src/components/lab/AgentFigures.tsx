import { useEffect, useRef, useState } from "react";

/** Three premium "agentic figure" candidates. All SVG with multi-layer
 *  animation, gradients, glow — no flat ASCII. Each renders live in
 *  the lab so the motion is what you see, not a screenshot. */

// ── A · LIQUID FLASK ─────────────────────────────────────────────
// Real SVG glass flask. Layered: glass outline, liquid with surface
// ripple, suspended figure silhouette (breathes), bubbles rising
// procedurally, ambient phosphor wash. Premium because every layer
// is in motion at a different rhythm.

export function FigureFlask({ size = 1 }: { size?: number }) {
  const [bubbles, setBubbles] = useState<{ id: number; x: number; born: number }[]>([]);
  const nextId = useRef(0);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      while (!cancelled) {
        await new Promise((r) => setTimeout(r, 900 + Math.random() * 1800));
        if (cancelled) return;
        const id = nextId.current++;
        const x = 38 + Math.random() * 44;
        setBubbles((bs) => [...bs, { id, x, born: Date.now() }]);
        // Expire after 3.6s
        setTimeout(() => setBubbles((bs) => bs.filter((b) => b.id !== id)), 3600);
      }
    }
    tick();
    return () => { cancelled = true; };
  }, []);

  const W = 160 * size;
  const H = 200 * size;

  return (
    <svg width={W} height={H} viewBox="0 0 160 200" style={{ display: "block" }}>
      <defs>
        <linearGradient id="flask-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--color-accent)" stopOpacity="0.85" />
          <stop offset="1" stopColor="var(--color-accent)" stopOpacity="0.35" />
        </linearGradient>
        <linearGradient id="flask-liquid" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--color-accent)" stopOpacity="0.35" />
          <stop offset="1" stopColor="var(--color-accent)" stopOpacity="0.12" />
        </linearGradient>
        <radialGradient id="flask-ambient" cx="50%" cy="65%" r="55%">
          <stop offset="0" stopColor="var(--color-accent)" stopOpacity="0.18" />
          <stop offset="1" stopColor="var(--color-accent)" stopOpacity="0" />
        </radialGradient>
        <filter id="flask-glow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="2" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <clipPath id="flask-inside">
          {/* Inside of flask — used to clip liquid to body shape */}
          <path d="M 60 50 L 60 90 Q 30 110 30 140 Q 30 180 80 180 Q 130 180 130 140 Q 130 110 100 90 L 100 50 Z" />
        </clipPath>
      </defs>

      {/* ambient phosphor wash */}
      <rect x="0" y="0" width="160" height="200" fill="url(#flask-ambient)" />

      {/* liquid inside, animated ripple */}
      <g clipPath="url(#flask-inside)">
        <rect x="0" y="118" width="160" height="80" fill="url(#flask-liquid)" />
        <path
          d="M 0 118 Q 20 114 40 118 T 80 118 T 120 118 T 160 118 L 160 200 L 0 200 Z"
          fill="var(--color-accent)"
          opacity="0.22"
          style={{ animation: "liquid-wave 5.5s ease-in-out infinite" }}
        />
        <path
          d="M 0 122 Q 20 119 40 122 T 80 122 T 120 122 T 160 122 L 160 200 L 0 200 Z"
          fill="var(--color-accent)"
          opacity="0.12"
          style={{ animation: "liquid-wave 7s ease-in-out infinite reverse" }}
        />

        {/* the homunculus — small suspended silhouette */}
        <g
          style={{
            transformOrigin: "80px 145px",
            animation: "figure-breathe 4.2s ease-in-out infinite",
          }}
        >
          {/* head */}
          <circle cx="80" cy="135" r="6" fill="var(--color-accent)" opacity="0.8" filter="url(#flask-glow)" />
          {/* body */}
          <path
            d="M 80 142 L 80 158 M 75 148 L 85 148 M 78 158 L 75 168 M 82 158 L 85 168"
            stroke="var(--color-accent)"
            strokeWidth="1.4"
            strokeLinecap="round"
            opacity="0.7"
            filter="url(#flask-glow)"
          />
        </g>

        {/* bubbles */}
        {bubbles.map((b) => (
          <circle
            key={b.id}
            cx={b.x}
            r={1.4 + Math.random() * 1.2}
            fill="var(--color-accent)"
            opacity="0.55"
            style={{
              animation: "bubble-rise 3.6s ease-out forwards",
              filter: "drop-shadow(0 0 3px var(--color-accent-glow))",
            }}
          >
            <animate attributeName="cy" from="178" to="118" dur="3.6s" fill="freeze" />
          </circle>
        ))}
      </g>

      {/* glass outline (over everything) */}
      <path
        d="M 60 24 L 60 90 Q 30 110 30 140 Q 30 180 80 180 Q 130 180 130 140 Q 130 110 100 90 L 100 24"
        fill="none"
        stroke="url(#flask-glass)"
        strokeWidth="1.4"
        strokeLinecap="round"
        style={{ filter: "drop-shadow(0 0 4px var(--color-accent-glow))" }}
      />
      {/* stopper */}
      <rect x="58" y="14" width="44" height="12" rx="0" fill="none" stroke="url(#flask-glass)" strokeWidth="1.4" />
      <rect x="64" y="8" width="32" height="8" rx="0" fill="none" stroke="url(#flask-glass)" strokeWidth="1.2" opacity="0.7" />

      {/* meta marks — alchemical hash on the side */}
      <g opacity="0.45" stroke="var(--color-accent)" strokeWidth="0.6">
        <line x1="34" y1="148" x2="40" y2="148" />
        <line x1="34" y1="156" x2="40" y2="156" />
        <line x1="34" y1="164" x2="38" y2="164" />
      </g>

      <style>{`
        @keyframes liquid-wave {
          0%, 100% { transform: translateX(0); }
          50%      { transform: translateX(-4px); }
        }
        @keyframes figure-breathe {
          0%, 100% { transform: scale(1)   translateY(0); }
          50%      { transform: scale(1.05) translateY(-1px); }
        }
        @keyframes bubble-rise {
          0%   { opacity: 0; }
          15%  { opacity: 0.6; }
          90%  { opacity: 0.5; }
          100% { opacity: 0; }
        }
      `}</style>
    </svg>
  );
}

// ── B · WIREFRAME FIGURE ─────────────────────────────────────────
// Vitruvian-style line-drawn humanoid in a circle. Stroke pulses
// (the "thought"), occasional node along the body lights up like a
// neural firing. Premium because of layered linework, glow, gradient.

export function FigureWireframe({ size = 1 }: { size?: number }) {
  const W = 200 * size;
  const H = 220 * size;
  const [active, setActive] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      while (!cancelled) {
        await new Promise((r) => setTimeout(r, 1200 + Math.random() * 1800));
        if (cancelled) return;
        setActive(Math.floor(Math.random() * 7));
        await new Promise((r) => setTimeout(r, 420));
        if (cancelled) return;
        setActive(null);
      }
    }
    tick();
    return () => { cancelled = true; };
  }, []);

  // Anatomical node positions (head, shoulders, elbows, hips, knees)
  const nodes = [
    { x: 100, y: 40, r: 10 },                   // head
    { x: 100, y: 64, r: 0 },                    // neck (no glow)
    { x: 76,  y: 88, r: 4 },                    // left shoulder
    { x: 124, y: 88, r: 4 },                    // right shoulder
    { x: 100, y: 110, r: 3 },                   // heart
    { x: 88,  y: 150, r: 3 },                   // left hip
    { x: 112, y: 150, r: 3 },                   // right hip
  ];

  return (
    <svg width={W} height={H} viewBox="0 0 200 220" style={{ display: "block" }}>
      <defs>
        <radialGradient id="wf-aura" cx="50%" cy="50%" r="55%">
          <stop offset="0" stopColor="var(--color-accent)" stopOpacity="0.10" />
          <stop offset="1" stopColor="var(--color-accent)" stopOpacity="0" />
        </radialGradient>
        <filter id="wf-glow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="1.6" />
        </filter>
      </defs>

      <rect width="200" height="220" fill="url(#wf-aura)" />

      {/* outer breathing circle */}
      <circle
        cx="100" cy="110" r="92"
        fill="none"
        stroke="var(--color-accent)" strokeWidth="0.8" opacity="0.35"
        style={{ animation: "wf-circle 6s ease-in-out infinite", transformOrigin: "100px 110px" }}
      />
      <circle
        cx="100" cy="110" r="80"
        fill="none"
        stroke="var(--color-accent)" strokeWidth="0.4" opacity="0.2"
      />

      {/* body — outstretched arms + legs, vitruvian */}
      <g
        stroke="var(--color-accent)"
        strokeWidth="1.4"
        strokeLinecap="round"
        fill="none"
        style={{
          filter: "drop-shadow(0 0 3px var(--color-accent-glow))",
          animation: "wf-breathe 4.6s ease-in-out infinite",
          transformOrigin: "100px 110px",
        }}
      >
        {/* head */}
        <circle cx="100" cy="40" r="10" />
        {/* spine */}
        <line x1="100" y1="50" x2="100" y2="150" />
        {/* arms outstretched */}
        <line x1="100" y1="64" x2="48"  y2="92" />
        <line x1="100" y1="64" x2="152" y2="92" />
        {/* legs */}
        <line x1="100" y1="150" x2="76"  y2="200" />
        <line x1="100" y1="150" x2="124" y2="200" />
        {/* hips */}
        <line x1="88"  y1="150" x2="112" y2="150" />
      </g>

      {/* nodes — neural firing markers */}
      {nodes.map((n, i) => (
        n.r > 0 && (
          <circle
            key={i}
            cx={n.x} cy={n.y} r={n.r}
            fill={active === i ? "var(--color-accent)" : "transparent"}
            stroke="var(--color-accent)"
            strokeWidth="1"
            style={{
              filter: active === i ? "drop-shadow(0 0 8px var(--color-accent-glow))" : "none",
              transition: "fill 200ms ease-out, filter 200ms ease-out",
            }}
          />
        )
      ))}

      <style>{`
        @keyframes wf-circle {
          0%, 100% { transform: scale(1); opacity: 0.35; }
          50%      { transform: scale(1.02); opacity: 0.5; }
        }
        @keyframes wf-breathe {
          0%, 100% { transform: scale(1); }
          50%      { transform: scale(1.012); }
        }
      `}</style>
    </svg>
  );
}

// ── C · GENERATIVE SIGIL ─────────────────────────────────────────
// Multi-layer alchemical sigil: outer ring with tick marks, middle
// rotating glyph, inner core that pulses. Multiple layers rotating
// at different speeds. Much more depth than the earlier flat version.

export function FigureSigil({ size = 1 }: { size?: number }) {
  const W = 200 * size;
  const H = 200 * size;
  const ticks = Array.from({ length: 36 }, (_, i) => i * 10);

  return (
    <svg width={W} height={H} viewBox="0 0 200 200" style={{ display: "block" }}>
      <defs>
        <radialGradient id="sig-aura" cx="50%" cy="50%" r="55%">
          <stop offset="0" stopColor="var(--color-accent)" stopOpacity="0.15" />
          <stop offset="1" stopColor="var(--color-accent)" stopOpacity="0" />
        </radialGradient>
      </defs>

      <rect width="200" height="200" fill="url(#sig-aura)" />

      {/* outer tick ring — slow rotation */}
      <g
        style={{
          transformOrigin: "100px 100px",
          animation: "sig-rotate-slow 120s linear infinite",
        }}
      >
        {ticks.map((deg) => (
          <line
            key={deg}
            x1="100" y1="6" x2="100" y2={deg % 30 === 0 ? 14 : 10}
            stroke="var(--color-accent)"
            strokeWidth={deg % 90 === 0 ? 1.4 : 0.6}
            opacity={deg % 30 === 0 ? 1 : 0.4}
            transform={`rotate(${deg} 100 100)`}
            style={{ filter: deg % 90 === 0 ? "drop-shadow(0 0 4px var(--color-accent-glow))" : "none" }}
          />
        ))}
        <circle cx="100" cy="100" r="92" fill="none" stroke="var(--color-accent)" strokeWidth="1" opacity="0.4" />
      </g>

      {/* middle ring with glyph — faster, reverse rotation */}
      <g
        style={{
          transformOrigin: "100px 100px",
          animation: "sig-rotate-fast 40s linear infinite reverse",
        }}
      >
        <circle cx="100" cy="100" r="68" fill="none" stroke="var(--color-accent)" strokeWidth="0.8" opacity="0.6" />
        <circle cx="100" cy="100" r="58" fill="none" stroke="var(--color-accent)" strokeWidth="0.4" opacity="0.4" />
        {/* mercurial bar — head */}
        <line x1="100" y1="38" x2="100" y2="62" stroke="var(--color-accent)" strokeWidth="1.4" style={{ filter: "drop-shadow(0 0 3px var(--color-accent-glow))" }} />
        {/* triangle (water/dissolve) */}
        <path d="M 76 80 L 124 80 L 100 130 Z" fill="none" stroke="var(--color-accent)" strokeWidth="1.4" style={{ filter: "drop-shadow(0 0 3px var(--color-accent-glow))" }} />
        {/* four corner marks */}
        {[0, 90, 180, 270].map((a) => (
          <g key={a} transform={`rotate(${a} 100 100)`}>
            <line x1="100" y1="68" x2="100" y2="72" stroke="var(--color-accent)" strokeWidth="0.8" />
          </g>
        ))}
      </g>

      {/* core — pulsing dot, the homunculus */}
      <g style={{ transformOrigin: "100px 100px" }}>
        <circle
          cx="100" cy="100" r="6"
          fill="var(--color-accent)"
          style={{
            filter: "drop-shadow(0 0 12px var(--color-accent-glow))",
            animation: "sig-core 2.4s ease-in-out infinite",
            transformOrigin: "100px 100px",
          }}
        />
        <circle cx="100" cy="100" r="14" fill="none" stroke="var(--color-accent)" strokeWidth="0.6" opacity="0.5" />
      </g>

      <style>{`
        @keyframes sig-rotate-slow { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        @keyframes sig-rotate-fast { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        @keyframes sig-core {
          0%, 100% { transform: scale(1);   opacity: 1; }
          50%      { transform: scale(1.35); opacity: 0.8; }
        }
      `}</style>
    </svg>
  );
}
