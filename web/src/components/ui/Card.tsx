import type { ReactNode } from "react";
import clsx from "clsx";

interface Props {
  children: ReactNode;
  className?: string;
  hoverable?: boolean;
  onClick?: () => void;
}

export function Card({ children, className, hoverable, onClick }: Props) {
  return (
    <div
      onClick={onClick}
      className={clsx(
        "bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-[8px]",
        hoverable && "hover:bg-[var(--color-surface-3)] hover:border-[var(--color-border-strong)] transition-colors cursor-pointer",
        className,
      )}
    >
      {children}
    </div>
  );
}
