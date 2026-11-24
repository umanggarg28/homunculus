import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useWaker } from "@/hooks/useWaker";
import { InstallButton } from "./InstallButton";
import { Mark } from "./Mark";
import { TAB_ICONS } from "./tabIcons";

type Later = "goals" | "library";

// Parts of the product that are planned but not built yet. They stay in the
// navigation so it does not move when they arrive, and say plainly what they will be.
const LATER: Record<Later, string> = {
  goals: "Goals aren’t built yet. They will show what the agent is working toward for you.",
  library: "Library isn’t built yet. It will hold what the agent has made for you.",
};
const NOTE_MS = 4200;
const title = (word: string) => word[0].toUpperCase() + word.slice(1);

export type Screen = "now" | "memory";

interface Note {
  text: string;
  left: number;
  top?: number;
  bottom?: number;
}

/** The short note a planned destination shows instead of opening. */
export function useLaterNote() {
  const [note, setNote] = useState<Note | null>(null);
  const timer = useRef<number>();
  useEffect(() => {
    const close = (e: KeyboardEvent) => e.key === "Escape" && setNote(null);
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, []);
  const show = (kind: Later, from: HTMLElement) => {
    const r = from.getBoundingClientRect();
    const inTabBar = !!from.closest(".tabbar");
    // On a phone the note sits wholly above the composer; on a desktop it hangs from its word.
    const composer = document.querySelector("textarea")?.getBoundingClientRect();
    const above = composer && composer.top > 0 ? composer.top : r.top;
    setNote(
      inTabBar
        ? { text: LATER[kind], left: 16, bottom: innerHeight - above + 12 }
        : { text: LATER[kind], left: Math.max(16, Math.min(r.left - 4, innerWidth - 368)), top: r.bottom + 10 },
    );
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setNote(null), NOTE_MS);
  };
  const view = note && (
    <p className="nav-note" role="status" style={{ left: note.left, top: note.top, bottom: note.bottom }}>
      {note.text}
    </p>
  );
  return { show, view };
}

type OnLater = (kind: Later, from: HTMLElement) => void;

/** The bar across the top of every screen: the mark, the destinations, and the status pill. */
export function TopBar({ current, pill, onLater }: { current: Screen; pill: string; onLater: OnLater }) {
  return (
    <header className="bar">
      <div className="bar-in">
        <Link className="wordmark" to="/" aria-label="Homunculus, home">
          <Mark />
          Homunculus
        </Link>
        <MainNav current={current} onLater={onLater} />
        <WakerControls pill={pill} />
        <InstallButton />
      </div>
    </header>
  );
}

/** Pause and Wake now, and the status pill, which says when the agent is working on its
 *  own. Without a waker on the server, only the pill shows. */
function WakerControls({ pill }: { pill: string }) {
  const { status, pause, resume, wakeNow } = useWaker();
  const own = status?.working && pill === "Ready";
  return (
    <>
      {status && (
        <div className="waker" role="group" aria-label="Work on its own">
          <button type="button" className="btn btn--quiet" aria-pressed={status.paused} onClick={status.paused ? resume : pause}>
            {status.paused ? "Resume" : "Pause"}
          </button>
          <button type="button" className="btn btn--quiet" disabled={status.working} onClick={wakeNow}>
            Wake now
          </button>
        </div>
      )}
      <p className="status" id="status" role="status">
        <span className="status-dot" aria-hidden="true" />
        <span id="status-text">{own ? "Working on its own" : status?.paused && pill === "Ready" ? "Paused" : pill}</span>
      </p>
    </>
  );
}

/** The top bar's destinations. */
export function MainNav({ current, onLater }: { current: Screen; onLater: OnLater }) {
  return (
    <nav className="nav" aria-label="Main">
      <Link to="/" aria-current={current === "now" ? "page" : undefined}>
        Now
      </Link>
      {(["goals", "library"] as const).map((kind) => (
        <a
          key={kind}
          href={`#${kind}`}
          onClick={(e) => {
            e.preventDefault();
            onLater(kind, e.currentTarget);
          }}
        >
          {title(kind)}
        </a>
      ))}
      <Link to="/memory/about" aria-current={current === "memory" ? "page" : undefined}>
        Memory
      </Link>
    </nav>
  );
}

/** The phone and tablet tab bar on screens other than Now. */
export function TabBar({ current, onLater }: { current: Screen; onLater: OnLater }) {
  const navigate = useNavigate();
  return (
    <nav className="tabbar" aria-label="Views">
      <button type="button" aria-current={current === "now" ? "page" : undefined} onClick={() => navigate("/")}>
        {TAB_ICONS.now}
        <span>Now</span>
      </button>
      <button type="button" className="tab-side" onClick={() => navigate("/", { state: { view: "side" } })}>
        {TAB_ICONS.activity}
        <span>Activity</span>
      </button>
      {(["goals", "library"] as const).map((kind) => (
        <button key={kind} type="button" onClick={(e) => onLater(kind, e.currentTarget)}>
          {TAB_ICONS[kind]}
          <span>{title(kind)}</span>
        </button>
      ))}
      <button type="button" aria-current={current === "memory" ? "page" : undefined} onClick={() => navigate("/memory/about")}>
        {TAB_ICONS.memory}
        <span>Memory</span>
      </button>
    </nav>
  );
}
