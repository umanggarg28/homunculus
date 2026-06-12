import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { MemoryEntry } from "@/lib/types";

/** The memory graph, drawn as a phosphor constellation: each entry is
 *  a star, each [[wikilink]] an edge. This is the page's "why is this
 *  not just a file list" moment — clustering is visible at a glance
 *  (project memories orbit their plan, feedback links into both).
 *
 *  Deliberate constraints:
 *  - No graph library. ~20 nodes is 40 lines of force layout; a
 *    dependency would cost more bundle than the feature.
 *  - Layout is deterministic (seeded PRNG from the node names), so the
 *    sky doesn't reshuffle on every visit — stars only move when the
 *    graph itself changes.
 *  - Monochrome discipline: all nodes phosphor, brightness follows
 *    degree. Type shows in the hover card, not as a color.
 */

interface Props {
  entries: MemoryEntry[];
}

const W = 720;
const H = 300;
const PAD = 36;

interface Node {
  entry: MemoryEntry;
  x: number;
  y: number;
  degree: number;
}

/** mulberry32 — tiny deterministic PRNG so layout is stable per dataset. */
function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function layout(entries: MemoryEntry[]): { nodes: Node[]; edges: [number, number][] } {
  const byName = new Map(entries.map((e, i) => [e.name.toLowerCase(), i]));
  const edgeSet = new Set<string>();
  const edges: [number, number][] = [];
  entries.forEach((e, i) => {
    for (const slug of e.links ?? []) {
      const j = byName.get(slug);
      // Dangling links are surfaced inline by MemoryContent; the sky
      // only draws stars that exist.
      if (j === undefined || j === i) continue;
      const key = i < j ? `${i}|${j}` : `${j}|${i}`;
      if (edgeSet.has(key)) continue;
      edgeSet.add(key);
      edges.push(i < j ? [i, j] : [j, i]);
    }
  });

  const degree = new Array(entries.length).fill(0);
  for (const [a, b] of edges) { degree[a] += 1; degree[b] += 1; }

  let seed = entries.length * 7919;
  for (const e of entries) for (let k = 0; k < e.name.length; k++) seed = (seed * 31 + e.name.charCodeAt(k)) | 0;
  const rand = seededRandom(seed);

  const xs = entries.map(() => (rand() - 0.5) * W * 0.8);
  const ys = entries.map(() => (rand() - 0.5) * H * 0.8);

  // Plain Fruchterman-Reingold-ish iteration: pairwise repulsion,
  // spring per edge, weak pull to center. O(n²·iters) — trivial here.
  for (let iter = 0; iter < 260; iter++) {
    const t = 1 - iter / 260;
    const fx = new Array(entries.length).fill(0);
    const fy = new Array(entries.length).fill(0);
    for (let i = 0; i < entries.length; i++) {
      for (let j = i + 1; j < entries.length; j++) {
        let dx = xs[i] - xs[j], dy = ys[i] - ys[j];
        const d2 = Math.max(dx * dx + dy * dy, 1);
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        const rep = 2600 / d2;
        fx[i] += dx * rep; fy[i] += dy * rep;
        fx[j] -= dx * rep; fy[j] -= dy * rep;
      }
      fx[i] -= xs[i] * 0.012;
      fy[i] -= ys[i] * 0.03; // pull harder vertically — canvas is wide
    }
    for (const [a, b] of edges) {
      let dx = xs[b] - xs[a], dy = ys[b] - ys[a];
      const d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const pull = (d - 70) * 0.015;
      dx /= d; dy /= d;
      fx[a] += dx * pull * d * 0.1; fy[a] += dy * pull * d * 0.1;
      fx[b] -= dx * pull * d * 0.1; fy[b] -= dy * pull * d * 0.1;
    }
    for (let i = 0; i < entries.length; i++) {
      xs[i] += Math.max(-8, Math.min(8, fx[i])) * t;
      ys[i] += Math.max(-8, Math.min(8, fy[i])) * t;
    }
  }

  // Normalize into the viewbox.
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const sx = (W - PAD * 2) / Math.max(maxX - minX, 1);
  const sy = (H - PAD * 2) / Math.max(maxY - minY, 1);
  const nodes = entries.map((entry, i) => ({
    entry,
    x: PAD + (xs[i] - minX) * sx,
    y: PAD + (ys[i] - minY) * sy,
    degree: degree[i],
  }));
  return { nodes, edges };
}

export function MemoryConstellation({ entries }: Props) {
  const navigate = useNavigate();
  const [hover, setHover] = useState<number | null>(null);

  const { nodes, edges } = useMemo(() => layout(entries), [entries]);

  // A sky with no lines is just scattered dots — skip until the agent
  // has actually cross-linked something.
  if (edges.length === 0) return null;

  const neighbors = new Set<number>();
  if (hover !== null) {
    neighbors.add(hover);
    for (const [a, b] of edges) {
      if (a === hover) neighbors.add(b);
      if (b === hover) neighbors.add(a);
    }
  }

  const hoverNode = hover !== null ? nodes[hover] : null;

  return (
    <div className="instrument-panel hm-panel-scan hm-panel-secondary mt-6">
      <div
        className="brut-meta px-4 py-3 flex justify-between"
        style={{ color: "var(--color-text-muted)", borderBottom: "1px solid var(--color-border)" }}
      >
        <span>── constellation · how memory cross-links</span>
        <span style={{ color: "var(--color-text-faint)" }}>
          {nodes.length} entries · {edges.length} links
        </span>
      </div>
      <div style={{ position: "relative" }}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          style={{ width: "100%", height: "auto", display: "block" }}
          onMouseLeave={() => setHover(null)}
        >
          {edges.map(([a, b], i) => {
            const lit = hover !== null && (a === hover || b === hover);
            return (
              <line
                key={i}
                x1={nodes[a].x} y1={nodes[a].y}
                x2={nodes[b].x} y2={nodes[b].y}
                stroke={lit ? "var(--color-accent)" : "var(--color-border-strong)"}
                strokeWidth={lit ? 1.2 : 1}
                opacity={hover === null ? 0.7 : lit ? 0.95 : 0.18}
                style={{ transition: "opacity 160ms, stroke 160ms" }}
              />
            );
          })}
          {nodes.map((n, i) => {
            const dimmed = hover !== null && !neighbors.has(i);
            const r = 2.5 + Math.min(n.degree, 6) * 0.9;
            const bright = n.degree >= 3;
            return (
              <g
                key={n.entry.filename}
                style={{ cursor: "pointer" }}
                opacity={dimmed ? 0.25 : 1}
                onMouseEnter={() => setHover(i)}
                onClick={() => navigate(`/memory/${encodeURIComponent(n.entry.filename)}`)}
              >
                {/* generous invisible hit area — 5px stars are hostile targets */}
                <circle cx={n.x} cy={n.y} r={14} fill="transparent" />
                <rect
                  x={n.x - r} y={n.y - r} width={r * 2} height={r * 2}
                  fill={hover === i ? "var(--color-accent)" : bright ? "var(--color-text)" : "var(--color-text-muted)"}
                  style={{
                    transition: "fill 160ms",
                    filter: hover === i || bright ? "drop-shadow(0 0 6px var(--color-accent-glow))" : "none",
                  }}
                />
                {(bright || hover === i) && (
                  <text
                    x={n.x} y={n.y + r + 12}
                    textAnchor="middle"
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 9,
                      letterSpacing: "0.08em",
                      fill: hover === i ? "var(--color-accent)" : "var(--color-text-faint)",
                      pointerEvents: "none",
                    }}
                  >
                    {shortName(n.entry.name)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {/* Hover card pinned to the panel corner, not the cursor —
            a chasing tooltip over a dense graph is unreadable. */}
        {hoverNode && (
          <div
            style={{
              position: "absolute",
              right: 10,
              top: 8,
              maxWidth: 300,
              padding: "6px 10px",
              background: "var(--color-bg)",
              border: "1px solid var(--color-border-strong)",
              fontFamily: "var(--font-mono)",
              pointerEvents: "none",
            }}
          >
            <div className="brut-label" style={{ color: "var(--color-accent)", letterSpacing: "0.14em" }}>
              {hoverNode.entry.name}
            </div>
            <div className="brut-meta" style={{ color: "var(--color-text-muted)", marginTop: 2 }}>
              {hoverNode.entry.type} · {hoverNode.degree} link{hoverNode.degree === 1 ? "" : "s"}
            </div>
            {hoverNode.entry.description && (
              <div className="brut-meta" style={{ color: "var(--color-text)", marginTop: 4, lineHeight: 1.45 }}>
                {hoverNode.entry.description.length > 110
                  ? hoverNode.entry.description.slice(0, 110) + "…"
                  : hoverNode.entry.description}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function shortName(name: string): string {
  const n = name.replace(/^(project|feedback|reference|user|skill)[-_]/, "");
  return n.length > 22 ? n.slice(0, 22) + "…" : n;
}
