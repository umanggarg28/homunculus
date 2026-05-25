import type { ReactNode } from "react";
import clsx from "clsx";

interface Props {
  children: ReactNode;
  className?: string;
  hoverable?: boolean;
  onClick?: () => void;
}

/** Brutalist card — hairline border, no radius, surface-1 fill. */
export function Card({ children, className, hoverable, onClick }: Props) {
  return (
    <div
      onClick={onClick}
      className={clsx(
        "bg-[var(--color-surface-1)] border border-[var(--color-border)]",
        hoverable && "hover:border-[var(--color-accent)] transition-colors cursor-pointer",
        className,
      )}
    >
      {children}
    </div>
  );
}
