import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { StatusMap } from "@/lib/types";

/** Poll /api/status every `intervalMs`. Returns the latest snapshot or
 * an empty object until the first response arrives. */
export function useStatus(intervalMs = 10_000): StatusMap {
  const [status, setStatus] = useState<StatusMap>({});

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await api.status();
        if (!cancelled) setStatus(data);
      } catch {
        /* swallow — transient network errors shouldn't break the UI */
      }
    };
    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs]);

  return status;
}
