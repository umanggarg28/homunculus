import type { ReactNode } from "react";

/** Consistent page chrome. Every route uses this so the header lands
 *  at the same x/y across the app. Pages render their PageHeader as
 *  the first child; everything else flows below. */
export function PageShell({ children }: { children: ReactNode }) {
  return (
    <div
      className="min-h-[calc(100vh-48px)] px-10 pt-6 pb-16"
      style={{ background: "var(--color-bg)", fontFamily: "var(--font-mono)" }}
    >
      <div className="max-w-[1200px] mx-auto">{children}</div>
    </div>
  );
}
