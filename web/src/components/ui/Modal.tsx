import { AnimatePresence, motion } from "framer-motion";
import { useEffect, type ReactNode } from "react";

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
  sm: "w-[400px]",
  md: "w-[520px]",
  lg: "w-[680px]",
};

export function Modal({ open, onClose, title, description, children, footer, size = "md" }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-50"
            style={{ background: "rgba(0,0,0,0.55)", backdropFilter: "blur(2px)" }}
            onClick={onClose}
          />
          <div className="fixed inset-0 z-50 flex items-center justify-center px-6 pointer-events-none">
            <motion.div
              initial={{ opacity: 0, y: 8, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 4, scale: 0.98 }}
              transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
              className={`${sizes[size]} max-w-[92vw] max-h-[85vh] flex flex-col pointer-events-auto`}
              style={{
                background: "var(--color-surface-2)",
                border: "1px solid var(--color-border-strong)",
                borderRadius: 10,
                boxShadow: "0 24px 48px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,255,255,0.02) inset",
              }}
            >
              {(title || description) && (
                <div className="px-6 pt-5 pb-4 border-b border-[var(--color-border)]">
                  {title && (
                    <h2 className="text-[16px] font-semibold text-[var(--color-text)] leading-tight">
                      {title}
                    </h2>
                  )}
                  {description && (
                    <p className="mt-1 text-[13px] text-[var(--color-text-dim)] leading-relaxed">
                      {description}
                    </p>
                  )}
                </div>
              )}

              <div className="px-6 py-5 overflow-y-auto flex-1">{children}</div>

              {footer && (
                <div className="px-6 py-4 border-t border-[var(--color-border)] flex justify-end gap-2">
                  {footer}
                </div>
              )}
            </motion.div>
          </div>
        </>
      )}
    </AnimatePresence>
  );
}
