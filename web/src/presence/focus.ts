/** A spotlight for the agent's key moments, ported from the design mock. Everything
 *  except the presence and the one thing that matters dims in place, to 40% over 1.2s.
 *  Nothing moves or leaves, so it reads as the agent pointing, never as the page
 *  breaking. A click or Escape ends it early. The CSS lives in design/layout.css under
 *  `html[data-focus]`. */

let timer: number | undefined;
let lit: Element[] = [];
// Each spotlight has a number, so a late clean-up never strips a newer one's panels.
let seq = 0;

const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

function unspot() {
  lit.forEach((e) => e.classList.remove("is-spot"));
  lit = [];
}

export function focus(kind: string, ms: number, spot: (Element | null)[], then?: () => void) {
  if (calm()) {
    then?.();
    return;
  }
  window.clearTimeout(timer);
  unspot();
  seq++;
  lit = spot.filter((e): e is Element => !!e);
  lit.forEach((e) => e.classList.add("is-spot"));
  document.documentElement.dataset.focus = kind;
  if (then) window.setTimeout(then, 650);
  timer = window.setTimeout(unfocus, ms);
}

export function unfocus() {
  window.clearTimeout(timer);
  if (!document.documentElement.dataset.focus) return;
  delete document.documentElement.dataset.focus;
  const mine = seq;
  window.setTimeout(() => {
    if (mine === seq) unspot();
  }, 1000);
}

/** End a spotlight on any press or on Escape. Returns a function that stops listening. */
export function listenForUnfocus(): () => void {
  const onPress = () => unfocus();
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape" && document.documentElement.dataset.focus) {
      unfocus();
      e.stopImmediatePropagation();
    }
  };
  document.addEventListener("pointerdown", onPress, true);
  document.addEventListener("keydown", onKey, true);
  return () => {
    document.removeEventListener("pointerdown", onPress, true);
    document.removeEventListener("keydown", onKey, true);
  };
}
