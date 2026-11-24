import { useCallback, useEffect, useState } from "react";
import { wakerApi, type WakerStatus } from "@/lib/api";

const REFRESH_MS = 15_000;

/** The waker's state, kept fresh, and the operator's controls for it. Null when the
 *  server has no waker. */
export function useWaker() {
  const [status, setStatus] = useState<WakerStatus | null>(null);
  const refresh = useCallback(async () => {
    try {
      setStatus(await wakerApi.status());
    } catch {
      // Unreachable server: keep what was last known.
    }
  }, []);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);
  const act = (call: () => Promise<WakerStatus | null>) => async () => {
    setStatus(await call());
    // Wake now starts a job a moment later; look again shortly so the pill follows it.
    setTimeout(refresh, 1500);
  };
  return {
    status,
    pause: act(wakerApi.pause),
    resume: act(wakerApi.resume),
    wakeNow: act(wakerApi.wakeNow),
  };
}
