import { useEffect, useRef, useState, type ReactNode } from "react";
import { AskPanel, askTitle } from "@/approvals/AskPanel";
import { useApprovals } from "@/hooks/useApprovals";
import { useAgentState, type AgentKind } from "@/state/agentState";
import { useJobsToday } from "@/hooks/useJobsToday";
import { Presence } from "@/presence/Presence";
import { listenForUnfocus, unfocus } from "@/presence/focus";
import { attachOverlay } from "@/presence/overlay";
import { Thread } from "@/thread/Thread";
import { InstallButton } from "./InstallButton";
import { playOpening } from "./opening";
import { TAB_ICONS } from "./tabIcons";
import { voiceOf } from "./voice";

// The screen's data-state, which the design's CSS reads: the presence's glow, the
// status dot and the live activity row all follow it.
const SCREEN_STATE: Record<AgentKind, string> = {
  ready: "idle",
  thinking: "working",
  working: "working",
  waiting: "waiting",
  done: "done",
  stuck: "stuck",
  stopped: "idle",
};

type View = "chat" | "side";
type Later = "goals" | "library" | "memory";

// Parts of the product that are planned but not built yet. They stay in the
// navigation so it does not move when they arrive, and say plainly what they will be.
const LATER: Record<Later, string> = {
  goals: "Goals aren’t built yet. They will show what the agent is working toward for you.",
  library: "Library isn’t built yet. It will hold what the agent has made for you.",
  memory: "Memory isn’t built yet. It will hold what the agent knows about you, and let you edit it.",
};
const NOTE_MS = 4200;

interface ShellProps {
  activity: ReactNode;
}

/** The console's one screen: a top bar with the status pill, the conversation in the
 *  middle under the agent's presence, and what needs approval and what the agent did
 *  beside it. Below 48rem the two columns share the screen through a tab bar. */
export function Shell({ activity }: ShellProps) {
  const state = useAgentState();
  const today = useJobsToday();
  const firstAsk = useApprovals().approvals.find((a) => a.status === "pending");
  const [now, setNow] = useState(() => new Date());
  const voice = voiceOf(state, today, now);
  const [view, setView] = useState<View>("chat");
  const [note, setNote] = useState<{ text: string; left: number; top?: number; bottom?: number } | null>(null);
  const noteTimer = useRef<number>();
  const bus = useRef<HTMLCanvasElement>(null);
  const fx = useRef<HTMLCanvasElement>(null);

  // The greeting follows the clock, so check it now and then.
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => (bus.current && fx.current ? attachOverlay(bus.current, fx.current) : undefined), []);
  useEffect(() => playOpening(), []);
  useEffect(() => listenForUnfocus(), []);
  // The top bar sits outside the app grid, and its status dot follows the state too.
  const screenState = SCREEN_STATE[state.kind];
  useEffect(() => {
    document.documentElement.dataset.state = screenState;
  }, [screenState]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => e.key === "Escape" && setNote(null);
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, []);

  const showLater = (kind: Later, from: HTMLElement) => {
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
    window.clearTimeout(noteTimer.current);
    noteTimer.current = window.setTimeout(() => setNote(null), NOTE_MS);
  };

  const switchView = (next: View) => {
    if (next === view) return;
    // A spotlight belongs to the view it was lit in.
    unfocus();
    const apply = () => setView(next);
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (calm || !document.startViewTransition) return apply();
    // A short crossfade with a slight slide in the direction you moved.
    document.documentElement.dataset.dir = next === "side" ? "fwd" : "back";
    document.startViewTransition(apply);
  };

  const len = voice.say.length > 110 ? "l" : voice.say.length > 62 ? "m" : "s";
  const waiting = state.pending > 0;

  return (
    <>
      <a className="skip" href="#ask-panel" hidden={!waiting}>
        Skip to what needs approval
      </a>
      <header className="bar">
        <div className="bar-in">
          <a className="wordmark" href="/" aria-label="Homunculus, home">
            <span className="mark" aria-hidden="true" />
            Homunculus
          </a>
          <nav className="nav" aria-label="Main">
            <a href="/" aria-current="page">
              Now
            </a>
            {(["goals", "library", "memory"] as const).map((kind) => (
              <a
                key={kind}
                href={`#${kind}`}
                onClick={(e) => {
                  e.preventDefault();
                  showLater(kind, e.currentTarget);
                }}
              >
                {kind[0].toUpperCase() + kind.slice(1)}
              </a>
            ))}
          </nav>
          <InstallButton />
          <p className="status" id="status" role="status">
            <span className="status-dot" aria-hidden="true" />
            <span id="status-text">{voice.pill}</span>
          </p>
        </div>
      </header>

      <div className="app no-rail" id="now" data-state={SCREEN_STATE[state.kind]} data-ask={waiting ? "yes" : undefined} data-view={view}>
        <main className="center panel col" aria-labelledby="stage-h">
          <div className="stage">
            <h1 className="visually-hidden" id="stage-h">
              Homunculus, now
            </h1>
            <Presence />
            <div className="voice">
              <p className="say" data-len={len} aria-live="polite">
                <span className="say-in" key={voice.say}>
                  {voice.say}
                </span>
              </p>
              <p className="say-meta">{voice.meta || " "}</p>
            </div>
          </div>
          <Thread ask={firstAsk ? askTitle(firstAsk) : null} onReview={() => switchView("side")} />
        </main>

        <aside className="side col" aria-label="Needs approval and activity">
          <section className="panel ask-panel" id="ask-panel" aria-labelledby="ask-h" tabIndex={-1}>
            <AskPanel />
          </section>
          <section className="panel" aria-label="Activity">
            {activity}
          </section>
        </aside>

        <nav className="tabbar" aria-label="Views">
          <button type="button" aria-current={view === "chat" ? "page" : undefined} onClick={() => switchView("chat")}>
            {TAB_ICONS.now}
            <span>Now</span>
          </button>
          <button
            type="button"
            className="tab-side"
            aria-current={view === "side" ? "page" : undefined}
            data-badge={waiting ? "" : undefined}
            onClick={() => switchView("side")}
          >
            {TAB_ICONS.activity}
            <span>Activity</span>
          </button>
          {(["goals", "library", "memory"] as const).map((kind) => (
            <button key={kind} type="button" onClick={(e) => showLater(kind, e.currentTarget)}>
              {TAB_ICONS[kind]}
              <span>{kind[0].toUpperCase() + kind.slice(1)}</span>
            </button>
          ))}
        </nav>
      </div>

      <canvas className="bus" ref={bus} aria-hidden="true" />
      <canvas className="fx" ref={fx} aria-hidden="true" />
      {note && (
        <p className="nav-note" role="status" style={{ left: note.left, top: note.top, bottom: note.bottom }}>
          {note.text}
        </p>
      )}
    </>
  );
}
