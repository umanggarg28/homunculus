import type { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}

/** A panel's heading in the design system: a quiet title, an optional note beside it,
 *  and any actions on the right. */
export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <div className="panel-head">
      <h2>{title}</h2>
      {subtitle && <span className="note">{subtitle}</span>}
      {actions && <div className="panel-actions">{actions}</div>}
    </div>
  );
}
