import { useEffect } from "react";
import { useRobotState } from "./useRobotState";
import type { RobotState } from "@/components/robot/HomunculusRobot";

/** Live agent state in the browser tab: the favicon carries a status
 *  dot, so a backgrounded Homunculus tab still tells you whether the
 *  agent is idle, working, or erroring — same trick Gmail uses for
 *  unread state.
 *
 *  Implementation notes:
 *  - SVG data-URI swap, no canvas. The base icon is already SVG and a
 *    data-URI rebuild keeps it crisp at any tab DPI; canvas would also
 *    need the webfont loaded before first paint to draw the glyph.
 *  - Hex literals, not CSS vars — a favicon renders outside the
 *    document, so tokens don't resolve there. Values mirror
 *    styles/index.css (accent #77FF3D, warning #FFB000, danger #FF5A3D).
 *  - State changes only, no animation loop: favicon writes are cheap
 *    but a rAF loop in every open tab is not.
 */

const GLYPH = "#7CFE00"; // matches public/icon.svg, slightly off the UI accent on purpose

const DOT: Record<RobotState, string | null> = {
  boot: null,
  idle: null, // no dot = nothing demands attention; the mark itself reads "on"
  listening: "#77FF3D",
  thinking: "#77FF3D",
  responding: "#77FF3D",
  success: "#77FF3D",
  working: "#FFB000",
  error: "#FF5A3D",
};

function faviconSvg(dot: string | null): string {
  const dotMarkup = dot
    ? `<circle cx="50" cy="50" r="11" fill="${dot}"/><circle cx="50" cy="50" r="14" fill="none" stroke="#030504" stroke-width="3"/>`
    : "";
  // Dot sits bottom-right over the glyph, ringed in bg so it separates
  // from the H at 16px.
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">` +
    `<rect width="64" height="64" fill="#030504"/>` +
    `<text x="50%" y="55%" text-anchor="middle" dominant-baseline="middle" font-family="JetBrains Mono, monospace" font-weight="700" font-size="38" fill="${GLYPH}">H</text>` +
    dotMarkup +
    `</svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export function useStatusFavicon(): void {
  const state = useRobotState();

  useEffect(() => {
    const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!link) return;
    link.href = faviconSvg(DOT[state]);
  }, [state]);

  // Restore the static icon if the app unmounts (auth gate, HMR).
  useEffect(() => {
    return () => {
      const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
      if (link) link.href = "/icon.svg";
    };
  }, []);
}
