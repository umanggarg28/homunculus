import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Mode = "plan" | "build";

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
      className="flex p-0.5 rounded-[6px]"
      style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-border)" }}
    >
      <ToggleBtn active={mode === "plan"} onClick={() => switchTo("plan")}>
        Plan
      </ToggleBtn>
      <ToggleBtn active={mode === "build"} onClick={() => switchTo("build")}>
        Build
      </ToggleBtn>
    </div>
  );
}

function ToggleBtn({
  children, active, onClick,
}: { children: React.ReactNode; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex-1 h-6 text-[11px] font-medium rounded-[4px] transition-colors"
      style={{
        background: active ? "var(--color-surface-4)" : "transparent",
        color: active ? "var(--color-text)" : "var(--color-text-muted)",
      }}
    >
      {children}
    </button>
  );
}
