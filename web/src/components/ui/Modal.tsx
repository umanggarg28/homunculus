import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface Props {
  open: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}

const sizes: Record<"sm" | "md" | "lg", string> = {
  sm: "w-[420px]",
  md: "w-[540px]",
  lg: "w-[720px]",
};

/** Brutalist modal — hard edges, accent border, no rounded corners,
 *  no float animation. Title rendered as `$ TITLE`. */
export function Modal({ open, onClose, title, description, children, footer, size = "md" }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <>
      <div
        className="fixed inset-0 z-50"
        style={{ background: "rgba(5,5,5,0.78)", backdropFilter: "blur(2px)" }}
        onClick={onClose}
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center px-6 pointer-events-none">
        <div
          className={`${sizes[size]} max-w-[92vw] max-h-[85vh] flex flex-col pointer-events-auto`}
          style={{
            background: "var(--color-bg)",
            border: "1px solid var(--color-accent)",
            boxShadow: "0 0 32px var(--color-accent-glow)",
            fontFamily: "var(--font-mono)",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {(title || description) && (
            <div
              className="px-5 pt-4 pb-3"
              style={{ borderBottom: "1px solid var(--color-border)" }}
            >
              {title && (
                <h2 className="text-[14px] font-bold uppercase tracking-[0.04em]" style={{ color: "var(--color-text)", margin: 0 }}>
                  <span style={{ color: "var(--color-accent)" }}>$</span> {title}
                </h2>
              )}
              {description && (
                <p className="mt-2 text-[11px] uppercase tracking-[0.12em]" style={{ color: "var(--color-text-muted)" }}>
                  ── {description}
                </p>
              )}
            </div>
          )}

          <div className="px-5 py-4 overflow-y-auto flex-1">{children}</div>

          {footer && (
            <div
              className="px-5 py-3 flex justify-end gap-2"
              style={{ borderTop: "1px solid var(--color-border)" }}
            >
              {footer}
            </div>
          )}
        </div>
      </div>
    </>,
    document.body,
  );
}
