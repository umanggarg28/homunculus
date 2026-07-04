import { useState, type ReactNode } from "react";

/** In-place hover tip for DENSE data cells (sparkline ticks, heat-map
 *  cells, activity-strip columns) — wears the same .hm-tooltip shell as
 *  the portal Tooltip so every hint in the app is one design.
 *
 *  Use the portal `Tooltip` for single triggers (badges, buttons,
 *  truncated text). Use DataTip inside cell loops: no portal, no
 *  reposition listeners — just an absolutely-positioned bubble above
 *  the cell, cheap enough for hundreds of instances.
 */
export function DataTip({ tip, children }: { tip: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <span
      style={{ position: "relative", display: "inline-block" }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      {children}
      {open && (
        <span
          role="tooltip"
          className="hm-tooltip"
          style={{
            position: "absolute",
            bottom: "calc(100% + 6px)",
            left: "50%",
            transform: "translateX(-50%)",
            whiteSpace: "nowrap",
            pointerEvents: "none",
            zIndex: 60,
            display: "block",
          }}
        >
          {tip}
        </span>
      )}
    </span>
  );
}
