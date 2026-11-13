import { useEffect, useRef, useState } from "react";
import type { ToolStep } from "@/lib/toolSteps";
import { stepSubject, stepWords } from "./steps";

type Kind = "c" | "o" | "e" | "w" | "k";
type Tab = "term" | "files";

// A long result is cut to its first lines; the whole of it is in the activity log.
const MAX_LINES = 14;
const two = (n: number) => String(n).padStart(2, "0");

/** What one step looks like on its computer: the call, then what came back. */
export function termLines(step: ToolStep): [Kind, string][] {
  // The terminal shows the whole call; only the card clips it.
  const subject = stepSubject(step.arguments);
  const lines: [Kind, string][] = [["c", step.name === "shell" && subject ? subject : [step.name, subject].filter(Boolean).join(" ")]];
  if (step.outcome === "waiting") {
    lines.push(["w", step.answer === "hand off" ? "handed to you: only you can do this" : "held: needs your approval"]);
    return lines;
  }
  const out = step.result.split("\n").filter((l) => l.trim());
  const kind: Kind = step.outcome === "failed" ? "e" : "o";
  out.slice(0, MAX_LINES).forEach((l) => lines.push([kind, l]));
  if (out.length > MAX_LINES) lines.push(["o", `… ${out.length - MAX_LINES} more lines in Activity`]);
  return lines;
}

const TOUCHED: Record<string, string> = { read: "read", write: "written", edit: "changed", delete: "deleted", rename: "renamed" };

/** Which files a job touched and how, from its file steps. A path seen twice shows once,
 *  with what happened to it last. */
export function filesTouched(steps: ToolStep[]): [string, string, number][] {
  const seen = new Map<string, [string, string, number]>();
  steps.forEach((step, k) => {
    const what = TOUCHED[step.name];
    const path = stepSubject(step.arguments);
    if (!what || !path) return;
    if (step.outcome === "waiting") seen.set(path, [path, `wants it ${what}, needs your approval`, k]);
    else if (step.outcome === "ok") seen.set(path, [path, what, k]);
  });
  return [...seen.values()].sort((a, b) => a[0].localeCompare(b[0]));
}

interface LiveViewProps {
  steps: ToolStep[];
  running: boolean;
  /** The step to show, or null for the whole job. */
  step: number | null;
  onStep: (step: number | null) => void;
  onClose: () => void;
}

/** The agent's computer for one job: a sheet over the conversation with the terminal
 *  and the files, grouped by step. Picking a step fades the later ones. */
export function LiveView({ steps, running, step, onStep, onClose }: LiveViewProps) {
  const [tab, setTab] = useState<Tab>("term");
  const heading = useRef<HTMLHeadingElement>(null);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, []);
  // Keep the chosen step, or the newest line, in view.
  useEffect(() => {
    const el = body.current;
    if (!el) return;
    const at = el.querySelector(".is-at");
    if (at) at.scrollIntoView({ block: "start" });
    else el.scrollTop = el.scrollHeight;
  }, [step, steps.length, tab]);

  const later = (k: number) => (step !== null && k > step ? " is-later" : "");
  const atStep = (k: number) => (step !== null && k === step ? " is-at" : "");
  const sub = step !== null ? `Step ${step + 1}: ${stepWords(steps[step]).title}` : running ? "This job, live" : "This job";
  const files = filesTouched(steps);

  return (
    <section className="hud" role="dialog" aria-modal="false" aria-labelledby="hud-h">
      <div className="hud-head">
        <div className="hud-title">
          <h2 id="hud-h" tabIndex={-1} ref={heading}>
            Live view
          </h2>
          <span className="hud-sub">{sub}</span>
        </div>
        <div className="seg" role="tablist" aria-label="What to show">
          {(
            [
              ["term", "Terminal"],
              ["files", "Files"],
            ] as const
          ).map(([t, label]) => (
            <button key={t} role="tab" type="button" aria-selected={tab === t} onClick={() => setTab(t)}>
              {label}
            </button>
          ))}
        </div>
        <button className="btn btn--quiet" type="button" aria-keyshortcuts="Escape" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="hud-body typing" role="tabpanel" ref={body}>
        {tab === "term" ? (
          <div className="term">
            {steps.map((s, k) => (
              <div key={k} className={`grp${later(k)}${atStep(k)}`}>
                <button
                  type="button"
                  className={`gnum${s.outcome === "waiting" ? " is-ask" : ""}`}
                  aria-pressed={step === k}
                  aria-label={`Step ${k + 1}`}
                  onClick={() => onStep(step === k ? null : k)}
                >
                  {two(k + 1)}
                </button>
                <div className="glines">
                  {termLines(s).map(([kind, text], i) => (
                    <span key={i} className={`ln ln--${kind}`} style={{ ["--i" as string]: i }}>
                      {kind === "c" ? "$ " : kind === "w" ? "! " : ""}
                      {text}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : files.length ? (
          <ul className="list rows">
            {files.map(([path, what, k], i) => (
              <li key={path} className={`${later(k)}${atStep(k)} ln--${/approval/.test(what) ? "w" : "o"}`} style={{ ["--i" as string]: i }}>
                <span className="name mono">{path}</span>
                <span className="fig">{what}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="hud-empty">No files were read or written on this job.</p>
        )}
      </div>
      <p className="hud-foot">{step !== null ? "Later steps are faded. Pick a step number to move through the job." : ""}</p>
    </section>
  );
}
