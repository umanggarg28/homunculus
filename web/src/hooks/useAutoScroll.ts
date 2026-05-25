import { useEffect, useRef } from "react";

/** Chat-style scroll-stick. While `trigger` changes:
 *
 *   • If the user is within `stickThreshold` px of the viewport bottom,
 *     scroll-to-bottom smoothly. This is the streaming-chat behavior —
 *     as new chunks land, the view follows.
 *   • If the user has scrolled up beyond the threshold (i.e., is reading
 *     history), we leave them alone and don't pull them back down.
 *
 *  Pass the value that should drive scroll-stick (`messages`, the last
 *  message length, etc.). Returns nothing — call as a side effect.
 */
export function useAutoScroll(trigger: unknown, stickThreshold = 240): void {
  // Remember whether the user was near the bottom at the *start* of the
  // current scroll batch. Without this, mid-stream the gap can briefly
  // exceed threshold and we'd let go of stick.
  const stuckRef = useRef(true);

  useEffect(() => {
    const recompute = () => {
      const gap =
        document.body.scrollHeight - (window.innerHeight + window.scrollY);
      stuckRef.current = gap < stickThreshold;
    };
    window.addEventListener("scroll", recompute, { passive: true });
    recompute();
    return () => window.removeEventListener("scroll", recompute);
  }, [stickThreshold]);

  useEffect(() => {
    if (!stuckRef.current) return;
    // Two-frame raf so layout has settled before scrolling — without
    // this, mid-stream growth lags by one frame and the scroll-to-bottom
    // ends up a few px short.
    const id = requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        window.scrollTo({
          top: document.body.scrollHeight,
          behavior: "smooth",
        });
      });
    });
    return () => cancelAnimationFrame(id);
  }, [trigger]);
}
