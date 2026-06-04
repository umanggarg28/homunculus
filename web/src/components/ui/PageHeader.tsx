import type { ReactNode } from "react";
import { useRobotState } from "@/hooks/useRobotState";
import { Tooltip } from "@/components/ui/Tooltip";

interface PageHeaderProps {
  /** @deprecated alias for title — kept for older pages. */
  latin?: string;
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
}

const STATE_LABELS: Record<string, string> = {
  idle: "IDLE", boot: "INIT", listening: "LISTENING",
  thinking: "THINKING", working: "WORKING", responding: "RESPONDING",
  success: "DONE", error: "FAULT",
};

/** Brutalist page header — `$ TITLE` with subtitle, unit crumb, and optional actions. */
export function PageHeader({ latin, title, subtitle, actions }: PageHeaderProps) {
  const heading = title ?? latin;
  const robotState = useRobotState();
  const stateLabel = STATE_LABELS[robotState] ?? robotState.toUpperCase();
  const isActive = !["idle", "boot"].includes(robotState);

  return (
    <div
      className="page-header mb-6 flex items-baseline justify-between gap-6 pb-3"
      style={{ borderBottom: "1px solid var(--color-border)", fontFamily: "var(--font-mono)" }}
    >
      <style>{`
        .page-header {
          min-height: 42px;
        }
        @media (max-width: 820px) {
          .page-header {
            align-items: flex-start;
            flex-direction: column;
            gap: 10px;
          }
          .page-header .unit-crumb {
            white-space: normal;
          }
        }
        @media (max-width: 520px) {
          .page-header .page-heading-row {
            align-items: flex-start;
            flex-direction: column;
            gap: 4px;
          }
        }
      `}</style>
      {/* Left: title + subtitle */}
      <div className="page-heading-row flex items-baseline gap-3 min-w-0">
        {heading && (
          <h1 className="brut-h1 truncate" style={{ color: "var(--color-text)", margin: 0 }}>
            <span style={{ color: "var(--color-accent)" }}>$</span> {heading}
          </h1>
        )}
        {subtitle && (
          <div className="brut-label truncate" style={{ color: "var(--color-text-muted)" }}>
            ── {subtitle}
          </div>
        )}
      </div>

      {/* Right: unit crumb + actions */}
      <div className="shrink-0 flex items-center gap-4">
        <Tooltip
          text={<><strong>HMCL-01</strong> — Homunculus Unit 01, this instance's identifier (cosmetic; only relevant if you run more than one).<br /><strong>STATE</strong> — the agent's liveness: active when it's running a turn, idle otherwise.</>}
          placement="bottom"
        >
        <div
          className="unit-crumb"
          style={{
            fontSize: 9,
            letterSpacing: "0.18em",
            color: "var(--color-text-muted)",
            textTransform: "uppercase",
            whiteSpace: "nowrap",
            cursor: "help",
          }}
        >
          UNIT · <span style={{ color: "var(--color-accent)" }}>HMCL-01</span>
          {" · "}STATE ·{" "}
          <span style={{ color: isActive ? "var(--color-accent)" : "var(--color-text-muted)" }}>
            {stateLabel}
          </span>
        </div>
        </Tooltip>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
