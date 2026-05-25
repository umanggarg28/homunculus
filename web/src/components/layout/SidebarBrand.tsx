import { DotMatrixWordmark } from "./DotMatrixWordmark";

const NAME = "HOMUNCULUS";
const VERSION = "v0.6";
const TAGLINE = "LIVING ROBOT";

/** Sidebar brand — dot-matrix wordmark + version tagline.
 *
 *  The character components (canvas robot in SidebarRobot.tsx, ASCII
 *  face in SidebarAsciiCharacter.tsx / AsciiFace.tsx) remain in the
 *  tree for future use but are not mounted anywhere. */
export function SidebarBrand() {
  return (
    <div className="px-3 pt-4 pb-4" style={{ borderBottom: "1px solid var(--color-border)" }}>
      <DotMatrixWordmark text={NAME} dotSize={3} gap={1} />
      <div
        className="brut-label"
        style={{
          color: "var(--color-text-faint)",
          marginTop: 8,
          fontSize: 8.5,
          letterSpacing: "0.20em",
        }}
      >
        {VERSION} · {TAGLINE}
      </div>
    </div>
  );
}
