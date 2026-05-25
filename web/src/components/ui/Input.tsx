import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import clsx from "clsx";

/** Brutalist form fields — hairline border, no radius, accent on focus.
 *  Mono throughout. */
const fieldBase =
  "w-full bg-transparent text-[var(--color-text)] " +
  "placeholder:text-[var(--color-text-faint)] " +
  "border border-[var(--color-border)] " +
  "px-3 py-2 text-[13px] " +
  "focus:border-[var(--color-accent)] " +
  "transition-colors outline-none";

const monoStyle: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  caretColor: "var(--color-accent)",
  borderRadius: 0,
};

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, style, ...rest }, ref) => (
    <input ref={ref} className={clsx(fieldBase, className)} style={{ ...monoStyle, ...style }} {...rest} />
  ),
);
Input.displayName = "Input";

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, style, ...rest }, ref) => (
    <textarea
      ref={ref}
      className={clsx(fieldBase, "resize-y", className)}
      style={{ ...monoStyle, ...style }}
      {...rest}
    />
  ),
);
Textarea.displayName = "Textarea";

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  children: React.ReactNode;
}

export function Select({ className, style, children, ...rest }: SelectProps) {
  return (
    <select
      className={clsx(fieldBase, "appearance-none cursor-pointer", className)}
      style={{ ...monoStyle, ...style }}
      {...rest}
    >
      {children}
    </select>
  );
}

/** Form field wrapper — small uppercase label above, hint below. */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-4">
      <label
        className="block text-[10px] uppercase tracking-[0.16em] mb-2"
        style={{ color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}
      >
        ── {label}
      </label>
      {children}
      {hint && (
        <div
          className="mt-1.5 text-[10px] uppercase tracking-[0.12em]"
          style={{ color: "var(--color-text-faint)", fontFamily: "var(--font-mono)" }}
        >
          {hint}
        </div>
      )}
    </div>
  );
}
