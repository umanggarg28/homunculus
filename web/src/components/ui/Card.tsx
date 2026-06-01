import type { ReactNode } from "react";
import clsx from "clsx";

interface Props {
  children: ReactNode;
  className?: string;
  hoverable?: boolean;
  onClick?: () => void;
}

/** Instrument card — hairline border, dense surface, subtle depth. */
export function Card({ children, className, hoverable, onClick }: Props) {
  return (
    <div
      onClick={onClick}
      className={clsx(
        "instrument-panel",
        hoverable && "hover:border-[var(--color-border-bright)] transition-colors cursor-pointer",
        className,
      )}
    >
      {children}
    </div>
  );
}
