import { useEffect, useRef } from "react";

/** Returns a ref to attach to a scroll target; when `trigger` changes
 * and the user is near the bottom, scrolls to bottom smoothly. */
export function useAutoScroll<T extends HTMLElement>(trigger: unknown) {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const nearBottom =
      window.innerHeight + window.scrollY > document.body.offsetHeight - 200;
    if (nearBottom) {
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    }
  }, [trigger]);

  return ref;
}
