import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { broadcastPaused } from "@/hooks/useAgentPaused";
import { Tooltip } from "@/components/ui/Tooltip";

/** Operator kill switch. Real control, theatrical chrome.
 *
 *  Two-step fire: first click ARMS (hazard stripes, 4s window),
 *  second click HALTS. Backed by AgentControls.paused — the heartbeat
 *  checks it before any work, so while engaged there are no autonomous
 *  ticks, no reflection, no LLM spend. Chat stays alive: the switch
 *  kills autonomy, not conversation.
 */
export function KillSwitch() {
  const [paused, setPaused] = useState<boolean | null>(null);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const disarmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    api.agentControls()
      .then((c) => setPaused(Boolean((c as { paused?: boolean }).paused)))
      .catch(() => setPaused(false));
  }, []);

  const setPausedRemote = async (next: boolean) => {
    if (busy) return;
    setBusy(true);
    try {
      const c = await api.agentControlsUpdate({ paused: next } as Partial<{ paused: boolean }>);
      const v = Boolean((c as { paused?: boolean }).paused);
      setPaused(v);
      broadcastPaused(v);
    } finally {
      setBusy(false);
      setArmed(false);
      if (disarmTimer.current) clearTimeout(disarmTimer.current);
    }
  };

  const onClick = () => {
    if (paused === null || busy) return;
    if (paused) { void setPausedRemote(false); return; }
    if (!armed) {
      setArmed(true);
      if (disarmTimer.current) clearTimeout(disarmTimer.current);
      disarmTimer.current = setTimeout(() => setArmed(false), 4000);
      return;
    }
    void setPausedRemote(true);
  };

  if (paused === null) return null;

  const label = paused ? "■ HALTED — RESUME" : armed ? "CONFIRM KILL" : "KILL SWITCH";
  const danger = "var(--color-danger)";

  return (
    <Tooltip
      placement="right"
      text={
        paused
          ? "Heartbeat halted by operator. Click to resume autonomous operation."
          : "Halt all autonomous operation (heartbeat ticks, scheduled tasks, reflection). Chat stays up. Two-step: arm, then confirm."
      }
    >
    <button
      onClick={onClick}
      disabled={busy}
      className={`h-6 w-full text-[10px] uppercase tracking-[0.18em] transition-colors ${armed ? "hm-hazard" : ""}`}
      style={{
        fontFamily: "var(--font-mono)",
        border: `1px solid ${paused || armed ? danger : "color-mix(in srgb, var(--color-danger) 45%, transparent)"}`,
        background: armed ? "color-mix(in srgb, var(--color-danger) 18%, transparent)" : "transparent",
        color: paused || armed ? danger : "color-mix(in srgb, var(--color-danger) 70%, var(--color-text-muted))",
        textShadow: paused ? `0 0 8px ${danger}` : "none",
        animation: paused ? "hm-halt-pulse 1.6s ease-in-out infinite" : "none",
        cursor: busy ? "wait" : "pointer",
      }}
    >
      {label}
    </button>
    </Tooltip>
  );
}
