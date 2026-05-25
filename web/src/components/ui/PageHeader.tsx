import type { ReactNode } from "react";

interface PageHeaderProps {
  /** @deprecated alias for title — kept for older pages. */
  latin?: string;
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
}

/** Brutalist page header — `$ TITLE` with subtitle on the right
 *  in small uppercase, hairline rule underneath. */
export function PageHeader({ latin, title, subtitle, actions }: PageHeaderProps) {
  const heading = title ?? latin;
  return (
    <div
      className="mb-6 flex items-baseline justify-between gap-6 pb-3"
      style={{ borderBottom: "1px solid var(--color-border)", fontFamily: "var(--font-mono)" }}
    >
      <div className="flex items-baseline gap-3 min-w-0">
        {heading && (
          <h1 className="brut-h1 truncate" style={{ color: "var(--color-text)", margin: 0 }}>
            <span style={{ color: "var(--color-accent)" }}>$</span> {heading}
          </h1>
        )}
        {subtitle && (
          <div className="brut-label truncate" style={{ color: "var(--color-text-muted)" }}>
            ── {subtitle}
          </div>
        )}
      </div>
      {actions && <div className="shrink-0 flex items-center gap-2">{actions}</div>}
    </div>
  );
}
