import type { ReactNode } from "react";

interface Props {
  /** "── NO X YET" header. */
  header: string;
  /** 1-2 sentence explainer in the agent's voice. */
  body: ReactNode;
  /** Optional "── TRY ASKING IT" sample prompts. */
  samples?: string[];
  /** Optional CTA at the bottom: `[+ DO THING]`. */
  cta?: { label: string; onClick: () => void };
  /** Optional sub-section header for samples (defaults to "── TRY ASKING IT"). */
  samplesHeader?: string;
}

/** Brutalist empty-state frame used by every list page.
 *  Hairline border, mono everywhere, dim explainer, optional sample
 *  prompts as `▸ "…"` lines, optional accent-bordered CTA button. */
export function BrutalistEmpty({ header, body, samples, samplesHeader = "── TRY ASKING IT", cta }: Props) {
  return (
    <div
      className="p-7 mt-2"
      style={{
        border: "1px solid var(--color-border)",
        background: "var(--color-surface-1)",
        fontFamily: "var(--font-mono)",
      }}
    >
      <div
        className="text-[10px] uppercase tracking-[0.22em] mb-4"
        style={{ color: "var(--color-text-muted)" }}
      >
        ── {header}
      </div>
      <div
        className="text-[13px] leading-[1.7]"
        style={{ color: "var(--color-text-dim)", marginBottom: samples || cta ? 24 : 0 }}
      >
        {body}
      </div>

      {samples && samples.length > 0 && (
        <>
          <div
            className="text-[10px] uppercase tracking-[0.18em] mb-3"
            style={{ color: "var(--color-text-muted)" }}
          >
            {samplesHeader}
          </div>
          <div className="mb-6">
            {samples.map((s, i) => (
              <div
                key={i}
                className="py-[3px] text-[12.5px]"
                style={{ color: "var(--color-text-dim)" }}
              >
                <span style={{ color: "var(--color-accent)", marginRight: 8 }}>▸</span>
                &ldquo;{s}&rdquo;
              </div>
            ))}
          </div>
        </>
      )}

      {cta && (
        <button
          onClick={cta.onClick}
          className="text-[11px] uppercase tracking-[0.16em] px-3 py-2 transition-colors"
          style={{
            background: "transparent",
            color: "var(--color-accent)",
            border: "1px solid var(--color-accent)",
            fontFamily: "var(--font-mono)",
          }}
          onMouseEnter={(e) => {
            const el = e.currentTarget as HTMLButtonElement;
            el.style.background = "var(--color-accent)";
            el.style.color = "var(--color-bg)";
          }}
          onMouseLeave={(e) => {
            const el = e.currentTarget as HTMLButtonElement;
            el.style.background = "transparent";
            el.style.color = "var(--color-accent)";
          }}
        >
          {cta.label}
        </button>
      )}
    </div>
  );
}
