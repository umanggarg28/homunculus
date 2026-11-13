import type { ToolStep } from "@/lib/toolSteps";
import { seconds, stepWords, workTag } from "./steps";

interface WorkCardProps {
  steps: ToolStep[];
  /** True while the turn these steps belong to is still running. */
  running: boolean;
  /** How many steps were already shown, so only newer rows arrive with the beat. */
  seen: number;
  /** The step open in the live view, if any. */
  open: number | null;
  onOpen: (step: number | null) => void;
}

const two = (n: number) => String(n).padStart(2, "0");

/** The steps of one job, as a card under the reply that did them. A new row shows
 *  muted until the trace from the presence lands on it, then comes to full strength.
 *  Each row opens that step in the live view. */
export function WorkCard({ steps, running, seen, open, onOpen }: WorkCardProps) {
  const tag = workTag(steps, running);
  return (
    <div className="work" id="work">
      <div className="work-head">
        <span className={`tag tag--${tag.tone}`}>{tag.text}</span>
        <button type="button" className="linkbtn" onClick={() => onOpen(null)}>
          Open live view
        </button>
      </div>
      <ol className="steps">
        {steps.map((step, k) => {
          const w = stepWords(step);
          const latest = running && k === steps.length - 1;
          const cls = [
            "step",
            latest ? "is-now" : step.outcome === "failed" ? "is-bad" : step.outcome === "waiting" ? "is-wait" : "",
            k >= seen ? "is-new" : "",
            open === k ? "is-open" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <li key={k}>
              <button
                type="button"
                className={cls}
                data-step={k}
                aria-label={`Step ${k + 1}, ${w.title}. Open it in live view.`}
                onClick={() => onOpen(k)}
              >
                <span className="step-n">{two(k + 1)}</span>
                <span className="step-body">
                  <span className="step-title">{w.title}</span>
                  <span className="step-cmd">{w.cmd}</span>
                  {w.why && <span className="step-why">{w.why}</span>}
                </span>
                <span className="step-s">{seconds(step.durationMs)}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
