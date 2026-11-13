import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useTurnEvents } from "@/lib/turnEvents";

const REFRESH_MS = 60_000;

/** How many jobs finished today, by this browser's clock, read from the activity log.
 *  Null until the log has been read once, so nothing is claimed before it is known. */
export function useJobsToday(): number | null {
  const [count, setCount] = useState<number | null>(null);
  const { events } = useTurnEvents(1);

  const refresh = useCallback(async () => {
    try {
      const jobs = await api.activity();
      const today = new Date().toDateString();
      setCount(jobs.filter((j) => j.ended && new Date(j.ended).toDateString() === today).length);
    } catch {
      // Unreachable server: keep the last count rather than showing none.
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (events.length) refresh();
  }, [events, refresh]);

  return count;
}
