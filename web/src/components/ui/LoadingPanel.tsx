interface Props {
  /** "── PROBE X" header — same shape as BrutalistEmpty. */
  title: string;
  /** One-line explainer of what's loading. */
  detail?: string;
}

/** Brutalist loading frame — paired with BrutalistEmpty so a page's
 *  loading and empty states share the same hairline-bordered shell.
 *  Animation: a scanning bar across the bottom edge. */
export function LoadingPanel({ title, detail }: Props) {
  return (
    <div
      className="p-7 mt-2"
      style={{
        border: "1px solid var(--color-border)",
        background: "var(--color-surface-1)",
        fontFamily: "var(--font-mono)",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <style>{`
        @keyframes hm-loading-scan {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(100%); }
        }
      `}</style>
      <div
        className="text-[10px] uppercase tracking-[0.22em] mb-3"
        style={{ color: "var(--color-text-muted)" }}
      >
        ── {title}
      </div>
      {detail && (
        <div
          className="text-[12.5px] leading-[1.6]"
          style={{ color: "var(--color-text-dim)" }}
        >
          {detail}
        </div>
      )}
      <div
        aria-hidden
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: 2,
          background: "var(--color-border)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            width: "40%",
            background: "var(--color-accent)",
            boxShadow: "0 0 6px var(--color-accent-glow)",
            animation: "hm-loading-scan 1.4s linear infinite",
          }}
        />
      </div>
    </div>
  );
}
