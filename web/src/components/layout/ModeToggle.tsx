import { useEffect, useState } from "react";
import { api } from "@/lib/api";

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
      <ToggleBtn active={mode === "plan"} onClick={() => switchTo("plan")}>PLAN</ToggleBtn>
      <ToggleBtn active={mode === "build"} onClick={() => switchTo("build")}>BUILD</ToggleBtn>
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
