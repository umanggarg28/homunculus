import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import clsx from "clsx";

const fieldBase =
  "w-full bg-[var(--color-surface-2)] text-[var(--color-text)] " +
  "placeholder:text-[var(--color-text-faint)] " +
  "border border-[var(--color-border-strong)] " +
  "px-3 py-2 text-[13.5px] rounded-[6px] " +
  "focus:border-[var(--color-accent)] focus:ring-1 focus:ring-[var(--color-accent)] " +
  "transition-colors outline-none";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, ...rest }, ref) => (
    <input ref={ref} className={clsx(fieldBase, className)} {...rest} />
  ),
);
Input.displayName = "Input";

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...rest }, ref) => (
    <textarea
      ref={ref}
      className={clsx(fieldBase, "resize-none", className)}
      {...rest}
    />
  ),
);
Textarea.displayName = "Textarea";

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  children: React.ReactNode;
}

export function Select({ className, children, ...rest }: SelectProps) {
  return (
    <select className={clsx(fieldBase, "appearance-none cursor-pointer", className)} {...rest}>
      {children}
    </select>
  );
}

/** Form field wrapper — label above, input below, consistent spacing. */
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
      <label className="block text-[12px] font-medium text-[var(--color-text-dim)] mb-1.5">
        {label}
      </label>
      {children}
      {hint && (
        <div className="mt-1 text-[11.5px] text-[var(--color-text-muted)]">
          {hint}
        </div>
      )}
    </div>
  );
}
