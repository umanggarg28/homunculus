/** Dot-matrix HOMUNCULUS wordmark — the strongest brand asset.
 *  Renders the word as lit 5×3 cells against dark cells, like an
 *  LED departures board. Used as the sidebar logo. */

const GLYPHS: Record<string, string[]> = {
  H: ["101","101","111","101","101"],
  O: ["111","101","101","101","111"],
  M: ["101","111","111","101","101"],
  U: ["101","101","101","101","111"],
  N: ["101","111","111","111","101"],
  C: ["111","100","100","100","111"],
  L: ["100","100","100","100","111"],
  S: ["111","100","111","001","111"],
  "·": ["000","000","010","000","000"],
  " ": ["000","000","000","000","000"],
};

interface Props {
  text?: string;
  /** Edge length of each lit cell, px. Total height = 5 * dotSize + 4 * gap. */
  dotSize?: number;
  gap?: number;
  litColor?: string;
  dimColor?: string;
  /** If set, only the first N columns are lit (for boot ignition).
   *  Omit for normal fully-lit rendering. */
  revealColumns?: number;
  /** Optional inline style on the outer grid. */
  style?: React.CSSProperties;
  className?: string;
}

export function DotMatrixWordmark({
  text = "HOMUNCULUS",
  dotSize = 3,
  gap = 1,
  litColor = "var(--color-accent)",
  dimColor = "var(--color-text-faint)",
  revealColumns,
  style,
  className,
}: Props) {
  const cols: boolean[][] = [];
  for (const ch of text) {
    const g = GLYPHS[ch] ?? GLYPHS[" "];
    for (let c = 0; c < 3; c++) {
      const col: boolean[] = [];
      for (let r = 0; r < 5; r++) col.push(g[r][c] === "1");
      cols.push(col);
    }
    cols.push([false, false, false, false, false]); // letter spacing
  }

  return (
    <div
      aria-label={text}
      className={className}
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${cols.length}, ${dotSize}px)`,
        gridTemplateRows: `repeat(5, ${dotSize}px)`,
        gap: `${gap}px`,
        ...style,
      }}
    >
      {Array.from({ length: 5 }).map((_, r) =>
        cols.map((col, c) => {
          const revealed = revealColumns === undefined || c < revealColumns;
          const lit = col[r] && revealed;
          return (
            <span
              key={`${r}-${c}`}
              style={{
                gridColumn: c + 1,
                gridRow: r + 1,
                width: dotSize,
                height: dotSize,
                background: lit ? litColor : dimColor,
                opacity: lit ? 0.95 : 0.18,
                boxShadow: lit ? "0 0 4px var(--color-accent-glow)" : "none",
                transition: "opacity 0.18s ease, background 0.18s ease",
              }}
            />
          );
        }),
      )}
    </div>
  );
}
