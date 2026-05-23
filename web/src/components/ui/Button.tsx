import type { ButtonHTMLAttributes, ReactNode } from "react";
import clsx from "clsx";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
}

const base =
  "inline-flex items-center justify-center gap-1.5 font-medium " +
  "transition-colors disabled:opacity-40 disabled:cursor-not-allowed " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]";

const sizes: Record<Size, string> = {
  sm: "h-7 px-2.5 text-[12px] rounded-[4px]",
  md: "h-8 px-3 text-[13px] rounded-[6px]",
};

const variants: Record<Variant, string> = {
  primary:
    "bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-hover)]",
  secondary:
    "bg-[var(--color-surface-3)] text-[var(--color-text)] border border-[var(--color-border-strong)] hover:bg-[var(--color-surface-4)]",
  ghost:
    "bg-transparent text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-3)]",
  danger:
    "bg-transparent text-[var(--color-danger)] border border-[var(--color-border-strong)] hover:bg-[var(--color-surface-3)] hover:border-[var(--color-danger)]",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  children,
  ...rest
}: Props) {
  return (
    <button className={clsx(base, sizes[size], variants[variant], className)} {...rest}>
      {children}
    </button>
  );
}
