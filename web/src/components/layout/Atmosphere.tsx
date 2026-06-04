import { useEffect, useState } from "react";

/**
 * <Atmosphere/> — mounts the global CRT depth layers that can't live on
 * body pseudo-elements (index.css already uses body::before for scanlines
 * and body::after for the vertical roll).
 *
 * Adds: vignette, phosphor flicker, top tube-glow. All pointer-events:none,
 * fixed, above the scanline layer. Honors prefers-reduced-motion.
 *
 * Usage — mount ONCE near the app root (e.g. in DashboardShell), after
 * importing the stylesheet:
 *
 *   import "@/styles/atmosphere.css";
 *   import { Atmosphere } from "@/components/layout/Atmosphere";
 *   …
 *   <Atmosphere />
 */
interface Props {
  vignette?: boolean;
  flicker?: boolean;
  topLight?: boolean;
  breath?: boolean;
}

export function Atmosphere({ vignette = true, flicker = true, topLight = true, breath = true }: Props) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;

  return (
    <>
      {vignette && <div className="atmo-layer atmo-vignette" aria-hidden />}
      {topLight && <div className="atmo-layer atmo-toplight" aria-hidden />}
      {flicker && <div className="atmo-layer atmo-flicker" aria-hidden />}
      {breath && <div className="atmo-layer atmo-breath" aria-hidden />}
    </>
  );
}

/**
 * useReveal — stagger page content in on mount.
 * Spread the returned props on a container; children with `.reveal` animate in sequence.
 *
 *   const reveal = useReveal();
 *   <div {...reveal}>
 *     <h1 className="reveal">…</h1>
 *   </div>
 */
export function useReveal(delay = 50) {
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setInView(true), delay);
    return () => clearTimeout(t);
  }, [delay]);
  return { "data-reveal": "", className: inView ? "in" : "" };
}
