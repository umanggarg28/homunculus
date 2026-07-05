import { useEffect, useRef, useState } from "react";

/** Two-step arm/confirm for destructive actions — the kill switch's
 *  pattern, shared. First activation arms (caller restyles the control
 *  and relabels it "confirm …"); a second within the window executes;
 *  the arm decays automatically so an abandoned first click can't
 *  turn a later stray click destructive.
 *
 *  `armed` holds a caller-chosen key so one hook instance can guard
 *  several controls (e.g. a row's cancel AND delete) with only one
 *  armed at a time. */
export function useArmedAction(timeoutMs = 4000) {
  const [armed, setArmed] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };

  const arm = (key: string) => {
    clear();
    setArmed(key);
    timer.current = setTimeout(() => setArmed(null), timeoutMs);
  };

  const disarm = () => {
    clear();
    setArmed(null);
  };

  useEffect(() => clear, []);

  return { armed, arm, disarm };
}
