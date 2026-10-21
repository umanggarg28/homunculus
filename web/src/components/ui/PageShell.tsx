import type { ReactNode } from "react";

/** Consistent page chrome. Every route uses this so the header lands
 *  at the same x/y across the app. Pages render their PageHeader as
 *  the first child; everything else flows below. */
export function PageShell({ children }: { children: ReactNode }) {
  return (
    <div
      className="page-shell hm-page-enter min-h-[calc(100vh-48px)] px-10 pt-6 pb-16"
      style={{ background: "var(--color-bg)", fontFamily: "var(--font-mono)" }}
    >
      <style>{`
        .page-shell {
          position: relative;
        }
        .page-shell::before {
          content: "";
          position: absolute;
          left: 0;
          right: 0;
          top: 0;
          height: 1px;
          background: linear-gradient(90deg, transparent, var(--color-border-bright), transparent);
          opacity: 0.45;
          pointer-events: none;
        }
        @media (max-width: 980px) {
          .page-shell {
            padding-left: 24px;
            padding-right: 24px;
          }
        }
        @media (max-width: 640px) {
          .page-shell {
            padding-left: 14px;
            padding-right: 14px;
            padding-top: 14px;
            padding-bottom: 96px;
          }
        }
      `}</style>
      <div className="max-w-[1200px] mx-auto">{children}</div>
    </div>
  );
}
