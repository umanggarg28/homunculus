import type { ButtonHTMLAttributes, ReactNode } from "react";
import clsx from "clsx";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
}

/** Brutalist button — hard-edged, mono uppercase, hover inverts to accent. */
const base =
  "inline-flex items-center justify-center gap-1.5 uppercase tracking-[0.12em] " +
  "transition-colors disabled:opacity-40 disabled:cursor-not-allowed font-medium rounded-[2px]";

const sizes: Record<Size, string> = {
  sm: "h-7 px-3 text-[10px]",
  md: "h-8 px-4 text-[11px]",
};

const variants: Record<Variant, string> = {
  primary:
    "bg-[var(--color-accent)] text-[var(--color-bg)] border border-[var(--color-accent)] " +
    "shadow-[0_0_18px_var(--color-accent-dim)] hover:bg-[var(--color-accent-hover)] hover:border-[var(--color-accent-hover)]",
  secondary:
    "bg-transparent text-[var(--color-text-dim)] border border-[var(--color-border)] " +
    "hover:text-[var(--color-accent)] hover:border-[var(--color-accent)]",
  ghost:
    "bg-transparent text-[var(--color-text-muted)] border border-transparent " +
    "hover:text-[var(--color-accent)]",
  danger:
    "bg-transparent text-[var(--color-danger)] border border-[var(--color-danger)] " +
    "hover:bg-[var(--color-danger)] hover:text-[var(--color-bg)]",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  children,
  ...rest
}: Props) {
  return (
    <button
      className={clsx(base, sizes[size], variants[variant], className)}
      style={{ fontFamily: "var(--font-mono)" }}
      {...rest}
    >
      {children}
    </button>
  );
}
