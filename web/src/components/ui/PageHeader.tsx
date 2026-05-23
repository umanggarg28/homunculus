import type { ReactNode } from "react";

interface PageHeaderProps {
  /** @deprecated alias for title — kept for older pages. */
  latin?: string;
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
}

/** Standard page header used at the top of every dashboard page.
 *  Title left, optional actions right, subtitle below. */
export function PageHeader({ latin, title, subtitle, actions }: PageHeaderProps) {
  const heading = title ?? latin;
  return (
    <div className="mb-8 flex items-start justify-between gap-6">
      <div>
        {heading && (
          <h1
            className="text-[var(--color-text)]"
            style={{
              fontSize: 22,
              fontWeight: 600,
              letterSpacing: "-0.015em",
              lineHeight: 1.2,
            }}
          >
            {heading}
          </h1>
        )}
        {subtitle && (
          <div className="mt-1 text-[13px] text-[var(--color-text-muted)]">
            {subtitle}
          </div>
        )}
      </div>
      {actions && <div className="shrink-0 flex items-center gap-2">{actions}</div>}
    </div>
  );
}
