import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** Whether the operator kill switch is engaged (heartbeat halted).
 *
 *  KillSwitch broadcasts changes on a window event so every consumer
 *  (robot, status chrome) flips the moment the operator acts; the
 *  60s poll only covers out-of-band changes (another tab, curl).
 */
export const PAUSED_EVENT = "hm:paused";

export function broadcastPaused(paused: boolean): void {
  window.dispatchEvent(new CustomEvent(PAUSED_EVENT, { detail: paused }));
}

export function useAgentPaused(): boolean {
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api.agentControls()
        .then((c) => { if (!cancelled) setPaused(Boolean((c as { paused?: boolean }).paused)); })
        .catch(() => undefined);
    load();
    const poll = setInterval(load, 60_000);
    const onEvent = (e: Event) => setPaused(Boolean((e as CustomEvent).detail));
    window.addEventListener(PAUSED_EVENT, onEvent);
    return () => {
      cancelled = true;
      clearInterval(poll);
      window.removeEventListener(PAUSED_EVENT, onEvent);
    };
  }, []);

  return paused;
}
