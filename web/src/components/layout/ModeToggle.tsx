import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Tooltip } from "@/components/ui/Tooltip";

type Mode = "plan" | "build";

/** Brutalist mode toggle. Hard-edged, accent-inverted on active. */
export function ModeToggle() {
  const [mode, setMode] = useState<Mode | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.modeGet().then((r) => setMode(r.mode)).catch(() => setMode("build"));
  }, []);

  const switchTo = async (next: Mode) => {
    if (busy || next === mode) return;
    setBusy(true);
    try {
      const r = await api.modeSet(next);
      setMode(r.mode);
    } finally {
      setBusy(false);
    }
  };

  if (mode === null) return null;

  return (
    <div
      className="flex"
      style={{ border: "1px solid var(--color-border)", fontFamily: "var(--font-mono)" }}
    >
      <Tooltip
        text={<><strong>PLAN</strong> mode — read-only. Mutating tools (write_file, notify, create_task, python) return a structured refusal instead of running. Use it to ask the agent to think before it acts.</>}
        placement="top"
      >
        <ToggleBtn active={mode === "plan"} onClick={() => switchTo("plan")}>PLAN</ToggleBtn>
      </Tooltip>
      <Tooltip
        text={<><strong>BUILD</strong> mode — default. The agent can execute any tool. Switch to PLAN before sensitive work.</>}
        placement="top"
      >
        <ToggleBtn active={mode === "build"} onClick={() => switchTo("build")}>BUILD</ToggleBtn>
      </Tooltip>
    </div>
  );
}

function ToggleBtn({
  children, active, onClick,
}: { children: React.ReactNode; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex-1 h-6 text-[10px] uppercase tracking-[0.16em] transition-colors"
      style={{
        background: active ? "var(--color-accent)" : "transparent",
        color: active ? "var(--color-bg)" : "var(--color-text-muted)",
        border: "none",
      }}
    >
      {children}
    </button>
  );
}
