import { useRobotState } from "@/hooks/useRobotState";
import type { RobotState } from "@/components/robot/HomunculusRobot";

const STATES: { key: RobotState; label: string }[] = [
  { key: "idle",       label: "IDLE" },
  { key: "listening",  label: "LISTEN" },
  { key: "thinking",   label: "THINK" },
  { key: "working",    label: "WORK" },
  { key: "responding", label: "REPLY" },
  { key: "success",    label: "DONE" },
  { key: "error",      label: "ERROR" },
];

export function AgentStateBar() {
  const state = useRobotState();

  return (
    <div
      style={{
        position: "fixed",
        bottom: 14,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 50,
        display: "flex",
        gap: 4,
        background: "rgba(5,5,5,0.88)",
        border: "1px solid var(--color-border)",
        padding: 5,
        backdropFilter: "blur(6px)",
        pointerEvents: "none",
      }}
    >
      {STATES.map((s) => {
        const isActive = state === s.key || (state === "boot" && s.key === "idle");
        return (
          <div
            key={s.key}
            style={{
              background: isActive ? "var(--color-accent)" : "transparent",
              border: "1px solid transparent",
              color: isActive ? "var(--color-bg)" : "var(--color-text-muted)",
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              fontWeight: isActive ? 700 : 500,
              letterSpacing: "0.10em",
              padding: "6px 8px",
              textTransform: "uppercase",
              transition: "background 0.2s, color 0.2s",
              boxShadow: isActive ? "0 0 14px var(--color-accent-glow)" : "none",
            }}
          >
            {s.label}
          </div>
        );
      })}
    </div>
  );
}
