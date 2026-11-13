import { useEffect, useRef, useState } from "react";
import { api, type Approval } from "@/lib/api";
import { useApprovals } from "@/hooks/useApprovals";
import { stepSubject } from "@/thread/steps";
import { focus } from "@/presence/focus";
import { send as sendTrace } from "@/presence/overlay";

// Allowing takes a deliberate hold, so a stray click never runs anything.
const HOLD_MS = 600;
const NUDGE_MS = 1400;
const RECENT = 3;

const hhmm = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "";

/** A request in words: what would happen if you allowed it. */
export function askTitle(a: Approval): string {
  const what = stepSubject(a.arguments);
  if (a.kind === "hand off") return what ? `Over to you: ${a.tool} ${what}` : `Over to you: ${a.tool}`;
  const verbs: Record<string, string> = { delete: "Delete", write: "Write", edit: "Edit", rename: "Rename" };
  if (verbs[a.tool] && what) return `${verbs[a.tool]} ${what}?`;
  if (a.tool === "shell") return "Run this command?";
  return `Run ${a.tool}?`;
}

function pretty(args: string): string {
  try {
    return JSON.stringify(JSON.parse(args), null, 2);
  } catch {
    return args;
  }
}

const DECIDED: Record<Approval["status"], string> = { pending: "Waiting", approved: "Allowed", denied: "Not allowed", done: "Done by you" };

function Ask({ a, first }: { a: Approval; first: boolean }) {
  const { refresh } = useApprovals();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [holding, setHolding] = useState(false);
  const [nudged, setNudged] = useState(false);
  const timer = useRef<number>();
  const nudge = useRef<number>();

  const act = async (call: () => Promise<Approval>) => {
    setBusy(true);
    setError(null);
    try {
      await call();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  const allow = () => act(() => api.approve(a.id));
  const startHold = () => {
    if (busy) return;
    setHolding(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setHolding(false);
      allow();
    }, HOLD_MS);
  };
  const endHold = () => {
    if (!holding) return;
    window.clearTimeout(timer.current);
    setHolding(false);
    setNudged(true);
    window.clearTimeout(nudge.current);
    nudge.current = window.setTimeout(() => setNudged(false), NUDGE_MS);
  };

  // Keys for the request at the top: hold O to allow once, press D to not allow.
  useEffect(() => {
    if (!first || a.kind !== "ask") return;
    const typing = (e: KeyboardEvent) => (e.target as HTMLElement | null)?.closest?.("textarea, input, select");
    const down = (e: KeyboardEvent) => {
      if (typing(e) || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
      if (e.key.toLowerCase() === "o") {
        e.preventDefault();
        startHold();
      } else if (e.key.toLowerCase() === "d") {
        e.preventDefault();
        act(() => api.deny(a.id, note));
      }
    };
    const up = (e: KeyboardEvent) => e.key.toLowerCase() === "o" && endHold();
    document.addEventListener("keydown", down);
    document.addEventListener("keyup", up);
    return () => {
      document.removeEventListener("keydown", down);
      document.removeEventListener("keyup", up);
    };
  });

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const handOff = a.kind === "hand off";
  return (
    <div className="ask-body is-new" data-approval={a.id}>
      <h3 className="ask-title">{askTitle(a)}</h3>
      <p className="ask-note">
        {handOff ? "Only you can do this. Mark it done when you have, with a note if it helps." : `Asked at ${hhmm(a.created)}. Nothing runs until you allow it.`}
      </p>
      <pre className="ask-args">{pretty(a.arguments)}</pre>
      <label className="visually-hidden" htmlFor={`note-${a.id}`}>
        {handOff ? "Note for the agent" : "Reason, if you don't allow it"}
      </label>
      <input
        id={`note-${a.id}`}
        className="ask-input"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={handOff ? "Note for the agent (optional)" : "Reason, if you don’t allow it (optional)"}
      />
      <div className="actions">
        {handOff ? (
          <button className="btn btn--ink" type="button" disabled={busy} onClick={() => act(() => api.done(a.id, note))}>
            <span>Mark as done</span>
          </button>
        ) : (
          <button
            className={`btn btn--attn${holding ? " is-holding" : ""}${nudged ? " is-nudged" : ""}`}
            type="button"
            data-hold="once"
            disabled={busy}
            aria-keyshortcuts={first ? "O" : undefined}
            style={{ ["--hold" as string]: `${HOLD_MS}ms` }}
            onPointerDown={(e) => {
              if (e.button) return;
              e.preventDefault();
              startHold();
            }}
            onPointerUp={endHold}
            onPointerLeave={endHold}
            onPointerCancel={endHold}
            // Enter, Space and assistive technology press a button without a pointer;
            // they allow at once, because a hold is not something they can do.
            onClick={(e) => {
              if (e.detail === 0) allow();
            }}
          >
            <span className="hold-fill" aria-hidden="true" />
            <span>Hold to allow once</span>
            {first && (
              <span className="key-hint" aria-hidden="true">
                {nudged ? "Keep holding" : "Hold O"}
              </span>
            )}
          </button>
        )}
        <button className="btn" type="button" disabled={busy} aria-keyshortcuts={first && !handOff ? "D" : undefined} onClick={() => act(() => api.deny(a.id, note))}>
          <span>{handOff ? "Don’t do it" : "Don’t allow"}</span>
          {first && !handOff && (
            <span className="key-hint" aria-hidden="true">
              Press D
            </span>
          )}
        </button>
      </div>
      {error && <p className="ask-error">{error}</p>}
    </div>
  );
}

/** What needs the operator's approval, with the most recent decisions under it. A new
 *  request gets a short spotlight: the rest of the screen dims while the presence sends
 *  a trace to it. */
export function AskPanel() {
  const { approvals } = useApprovals();
  const pending = approvals.filter((a) => a.status === "pending");
  const recent = approvals.filter((a) => a.status !== "pending").slice(0, RECENT);
  const known = useRef<Set<string> | null>(null);

  useEffect(() => {
    const ids = new Set(pending.map((a) => a.id));
    // The first list is what was already waiting, so it does not count as new.
    const fresh = known.current ? [...ids].some((id) => !known.current?.has(id)) : false;
    known.current = ids;
    if (!fresh) return;
    const phone = matchMedia("(max-width: 48rem)").matches;
    const panel = document.getElementById("ask-panel");
    const target = phone ? document.querySelector(".ask-dock") : panel?.querySelector("[data-hold]") ?? panel;
    if (!phone) panel?.scrollIntoView({ block: "start" });
    focus("ask", 4000, [phone ? document.querySelector(".ask-dock") : panel, document.querySelector(".thread")], () => sendTrace(target ?? null, "attn"));
  }, [pending.map((a) => a.id).join()]);

  return (
    <>
      <div className="panel-head">
        <h2 id="ask-h">Needs approval</h2>
        <span className="note">{pending.length ? `${pending.length} waiting` : "None"}</span>
      </div>
      {pending.length ? (
        pending.map((a, i) => <Ask key={a.id} a={a} first={i === 0} />)
      ) : (
        <div className="ask-body">
          <p className="ask-note">Nothing needs approval right now.</p>
        </div>
      )}
      {recent.length > 0 && (
        <>
          <h3 className="ask-sub">Recently decided</h3>
          <ul className="list decided">
            {recent.map((a) => (
              <li key={a.id}>
                <span className="name">{askTitle(a)}</span>
                <span className="fig">
                  {DECIDED[a.status]} {hhmm(a.decided)}
                </span>
                <span className="line">{a.result ? `Result: ${a.result.split("\n")[0]}` : a.note ? `Note: ${a.note}` : a.follow_up === "replied" ? "The agent replied in the conversation." : ""}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
