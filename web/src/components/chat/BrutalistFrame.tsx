import { type ReactNode, useEffect, useRef, useState } from "react";

/** ASCII box-drawing frame with a centered label.
 *
 * Renders:
 *   ┌──── LABEL ────────────────────────────┐
 *   │ (children)                            │
 *   └───────────────────────────────────────┘
 *
 * The horizontal rules auto-fill to the container's width using
 * a ResizeObserver — no fixed character count needed.
 */
export function BrutalistFrame({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [cols, setCols] = useState(80);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      // ~7.4px per char at 12px JetBrains Mono — close enough.
      const px = el.clientWidth;
      setCols(Math.max(40, Math.floor(px / 7.4)));
    };
    update();
    const obs = new ResizeObserver(update);
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const labelText = `── ${label} `;
  const top = `┌${labelText}${"─".repeat(Math.max(2, cols - labelText.length - 2))}┐`;
  const bot = `└${"─".repeat(Math.max(2, cols - 2))}┘`;

  return (
    <div ref={ref} className="w-full">
      <pre
        className="m-0 whitespace-pre text-[12px] leading-[1.2]"
        style={{ color: "var(--color-border-strong)", fontFamily: "var(--font-mono)" }}
      >
        {top}
      </pre>
      <div className="px-1 py-5">{children}</div>
      <pre
        className="m-0 whitespace-pre text-[12px] leading-[1.2]"
        style={{ color: "var(--color-border-strong)", fontFamily: "var(--font-mono)" }}
      >
        {bot}
      </pre>
    </div>
  );
}
