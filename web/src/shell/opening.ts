/** The opening, played once on a session's first load (index.html decides which, before
 *  the first paint, by setting `data-intro` to "0" or "done"). The presence and its first
 *  words start alone at the centre of the screen, then dock at the top of the
 *  conversation while the panels unfold around them. A reload skips it, because a
 *  reload is when the operator wants to see the work, not the show.
 *
 *  Each stage is a value of `data-intro` on <html>, and the CSS in design/layout.css
 *  says what each stage shows. This file only moves through them on time, and centres
 *  the stage on the screen, which CSS cannot measure. Returns a function that stops it. */
import { drawBus } from "@/presence/overlay";

export function playOpening(): () => void {
  const root = document.documentElement;
  const stage = document.querySelector<HTMLElement>(".stage");
  if (root.dataset.intro !== "0" || !stage) return () => undefined;
  const timers: number[] = [];
  const at = (ms: number, fn: () => void) => timers.push(window.setTimeout(fn, ms));
  const phone = () => matchMedia("(max-width: 48rem)").matches;
  const SCALE = 1.12;
  let origin: { x: number; y: number; ox: number; oy: number } | null = null;

  // Measure the words themselves, not their boxes, which always span the full line.
  const ink = (el: Element) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    return range.getBoundingClientRect();
  };
  const centre = (instant: boolean) => {
    const core = document.querySelector(".core");
    const say = document.querySelector(".say .say-in") ?? document.querySelector(".say");
    const meta = document.querySelector(".say-meta");
    if (!core || !say) return;
    // Measure at rest with no transition running, or the browser reports a midpoint.
    const keep = stage.style.transition;
    stage.style.transition = "none";
    stage.style.transform = "none";
    const a = core.getBoundingClientRect();
    const b = ink(say);
    const m = meta?.textContent?.trim() ? ink(meta) : null;
    const s0 = stage.getBoundingClientRect();
    const ux = (Math.min(a.left, b.left) + Math.max(a.right, b.right, m ? m.right : 0)) / 2;
    const uy = (Math.min(a.top, b.top) + Math.max(a.bottom, b.bottom)) / 2;
    if (instant || !origin) origin = { x: ux, y: uy, ox: ux - s0.left, oy: uy - s0.top };
    // Where the pair's centre lands after scaling about the fixed origin, then the shift
    // that puts it at the centre of the screen.
    const sx = origin.x + (ux - origin.x) * SCALE;
    const sy = origin.y + (uy - origin.y) * SCALE;
    const o = origin;
    const place = () => {
      stage.style.setProperty("--ix", `${innerWidth / 2 - sx}px`);
      stage.style.setProperty("--iy", `${innerHeight / 2 - sy}px`);
      stage.style.transformOrigin = `${o.ox}px ${o.oy}px`;
    };
    stage.style.transform = "";
    if (instant) place();
    void stage.offsetWidth;
    stage.style.transition = keep;
    if (!instant) {
      // A short settle that always finishes before the dock begins.
      stage.style.transition = "transform 640ms cubic-bezier(0.33, 1, 0.68, 1)";
      void stage.offsetWidth;
      place();
      at(760, () => (stage.style.transition = keep));
    }
  };

  centre(true);
  // The first measure can run on the fallback font; measure again once the real one is in.
  document.fonts?.ready.then(() => {
    if (root.dataset.intro === "0") centre(true);
  });
  // The quieter line may arrive after the first paint (it counts today's jobs).
  at(1100, () => requestAnimationFrame(() => centre(false)));
  at(2100, () => {
    if (!phone()) {
      root.dataset.intro = "a";
      return;
    }
    // On a phone the stage changes shape when it docks, so the sphere flies from where
    // it was to where it lands, and the words fade back in beside it.
    const wrap = document.querySelector<HTMLElement>(".core-wrap");
    const voice = document.querySelector<HTMLElement>(".voice");
    if (!wrap || !voice) {
      root.dataset.intro = "a";
      return;
    }
    voice.style.transition = "opacity 160ms var(--ease-out)";
    voice.style.opacity = "0";
    at(170, () => {
      const first = wrap.getBoundingClientRect();
      stage.style.transition = "none";
      root.dataset.intro = "a";
      const lastBox = wrap.getBoundingClientRect();
      wrap.style.transformOrigin = "0 0";
      wrap.style.transform = `translate(${first.left - lastBox.left}px, ${first.top - lastBox.top}px) scale(${first.width / lastBox.width})`;
      void stage.offsetWidth;
      wrap.style.transition = "transform 1100ms var(--ease-out)";
      wrap.style.transform = "";
      voice.style.transition = "opacity 600ms var(--ease-out) 100ms";
      voice.style.opacity = "";
      at(1200, () => {
        wrap.style.transition = wrap.style.transformOrigin = "";
        voice.style.transition = "";
        stage.style.transition = "";
      });
    });
  });
  at(2400, () => (root.dataset.intro = "1"));
  at(2750, () => (root.dataset.intro = "2"));
  // The bus draws out from the presence as the panels reach their places.
  drawBus(0);
  at(2900, () => {
    const t0 = performance.now();
    const grow = (t: number) => {
      const k = Math.min(1, (t - t0) / 900);
      drawBus(1 - Math.pow(1 - k, 3));
      if (k < 1) requestAnimationFrame(grow);
    };
    requestAnimationFrame(grow);
  });
  at(3100, () => (root.dataset.intro = "3"));
  at(4300, () => (root.dataset.intro = "done"));

  return () => {
    timers.forEach(clearTimeout);
    // Stopped part-way (as React does once in development): show the docked layout.
    if (root.dataset.intro !== "0") root.dataset.intro = "done";
    drawBus(1);
  };
}
