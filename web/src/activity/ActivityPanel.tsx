import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Job, type Rating } from "@/lib/api";
import { useTurnEvents } from "@/lib/turnEvents";
import type { ToolStep } from "@/lib/toolSteps";
import { send as sendTrace } from "@/presence/overlay";
import { stepWords } from "@/thread/steps";

// The agent can work while you look elsewhere, so the list is read on a timer as well
// as after every turn event.
const REFRESH_MS = 10_000;
const REPLY_PREVIEW = 280;
const TITLE_MAX = 80;
// A column of the chart is one point per step, up to this many.
const COLUMN_MAX = 8;

const hhmm = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
};
const duration = (a: string, b: string) => {
  const s = Math.max(0, (Date.parse(b) - Date.parse(a)) / 1000);
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
};
const isToday = (iso: string | null) => !!iso && new Date(iso).toDateString() === new Date().toDateString();
const plural = (n: number, one: string, many: string) => (n === 1 ? `one ${one}` : `${n} ${many}`);

/** A job in one line: what was asked, or for a decision, that it was one. */
function jobTitle(job: Job): string {
  if (job.notice) return "Your decision";
  const m = (job.message ?? "No message was recorded for this job").split("\n")[0];
  return m.length > TITLE_MAX ? `${m.slice(0, TITLE_MAX - 1)}…` : m;
}

function jobLine(job: Job): string {
  const failed = job.steps.filter((s) => s.outcome === "failed").length;
  const parts = [job.steps.length ? (job.steps.length === 1 ? "One step" : `${job.steps.length} steps`) : "No steps"];
  if (failed) parts.push(failed === 1 ? "one failed" : `${failed} failed`);
  if (job.ended) parts.push(duration(job.started, job.ended));
  return parts.join(", ");
}

// An activity step has what the card needs, apart from how long it took.
const asStep = (s: Job["steps"][number]): ToolStep => ({ ...s, durationMs: 0 });

const PROMPT: Record<Rating, string> = { up: "What was good? (optional)", down: "What could be better? (optional)" };

/** Thumbs up or down, each with an optional reason. A later rating replaces an earlier one. */
function Rate({ job, onRated }: { job: Job; onRated: (job: Job) => void }) {
  const [choosing, setChoosing] = useState<Rating | null>(null);
  // Each new rating starts with no reason, so an old one is never sent with it.
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const current = job.feedback?.rating ?? null;
  const save = async (rating: Rating) => {
    setError(null);
    try {
      onRated(await api.feedback(job.job, rating, reason.trim() || undefined));
      setChoosing(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const thumb = (rating: Rating) => (
    <button
      type="button"
      className={`rate-btn${(choosing ?? current) === rating ? " is-on" : ""}`}
      aria-label={rating === "up" ? "good work" : "not good work"}
      aria-pressed={current === rating}
      onClick={() => {
        setChoosing(rating);
        setReason("");
        setError(null);
      }}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true" style={rating === "down" ? { transform: "rotate(180deg)" } : undefined}>
        <path d="M7 11v9H4v-9zM7 11l4-7c1.4 0 2.3 1 2 2.4L12.4 10H18a2 2 0 0 1 2 2.3l-1.2 6A2 2 0 0 1 16.8 20H7" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      </svg>
    </button>
  );
  if (!job.span) return null;
  return (
    <div className="rate">
      <div className="rate-row">
        <span className="rate-q">Was this good work?</span>
        {thumb("up")}
        {thumb("down")}
      </div>
      {job.feedback?.reason && choosing === null && <p className="rate-reason">“{job.feedback.reason}”</p>}
      {choosing && (
        <div className="rate-form">
          <input
            autoFocus
            className="ask-input"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save(choosing);
              if (e.key === "Escape") setChoosing(null);
            }}
            placeholder={PROMPT[choosing]}
            aria-label={PROMPT[choosing]}
          />
          <button type="button" className="btn btn--ink btn--quiet" onClick={() => save(choosing)}>
            <span>Save</span>
          </button>
        </div>
      )}
      {error && <p className="ask-error">{error}</p>}
    </div>
  );
}

/** Today's jobs as columns of points, one point per step, from the hour of the first
 *  job to now, so a busy hour spreads out instead of piling up at one edge. A failed
 *  job's column is red, a running one is lime, a decision is quiet. */
function DayChart({ jobs }: { jobs: Job[] }) {
  const today = jobs.filter((j) => isToday(j.ended ?? j.started));
  const first = today.reduce((m, j) => Math.min(m, Date.parse(j.started)), Date.now());
  const start = new Date(first);
  start.setMinutes(0, 0, 0);
  const span = Math.max(60 * 60_000, Date.now() - start.getTime());
  const W = 100;
  return (
    <div className="day">
      <svg className="day-chart" viewBox={`0 0 ${W} 40`} preserveAspectRatio="none" role="img" aria-label={`Today’s jobs since ${hhmm(start.toISOString())}, by the time each finished: ${today.length}.`}>
        {today.map((j) => {
          const at = Date.parse(j.ended ?? j.started) - start.getTime();
          const x = 1 + ((W - 2) * at) / span;
          const n = Math.max(1, Math.min(COLUMN_MAX, j.steps.length));
          const tone = !j.ended ? "live" : j.error || j.steps.some((s) => s.outcome === "failed") ? "bad" : j.notice ? "quiet" : "ok";
          return Array.from({ length: n }, (_, k) => <rect key={`${j.job}-${k}`} className={`pt pt--${tone}`} x={x - 0.6} y={37 - k * 4.4} width={1.2} height={2.6} rx={0.3} />);
        })}
        <line className="day-base" x1="0" x2={W} y1="39.5" y2="39.5" />
      </svg>
      <div className="day-axis" aria-hidden="true">
        <span>{hhmm(start.toISOString())}</span>
        <span>Now</span>
      </div>
    </div>
  );
}

/** What the agent did, newest first, read from the activity log. Each job opens into
 *  its steps, its reply and a rating, so the agent can learn what good work looks like. */
export function ActivityPanel() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const { events } = useTurnEvents(1);
  const list = useRef<HTMLOListElement>(null);
  const lastEnded = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setJobs(await api.activity());
    } catch {
      // Unreachable server: keep the last list rather than showing an empty one.
    }
  }, []);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    if (events.length) refresh();
  }, [events, refresh]);

  // When a job finishes, its result travels from the presence to its row.
  useEffect(() => {
    const newest = jobs?.find((j) => j.ended);
    if (!newest) return;
    if (lastEnded.current !== null && lastEnded.current !== newest.job) {
      sendTrace(list.current?.querySelector(`[data-job="${newest.job}"]`) ?? null, "ok");
    }
    lastEnded.current = newest.job;
  }, [jobs]);

  const replace = (updated: Job) => setJobs((now) => now?.map((j) => (j.job === updated.job ? updated : j)) ?? null);
  const today = (jobs ?? []).filter((j) => j.ended && isToday(j.ended));
  const steps = today.reduce((n, j) => n + j.steps.length, 0);
  const failed = today.reduce((n, j) => n + j.steps.filter((s) => s.outcome === "failed").length, 0);

  return (
    <>
      <div className="panel-head">
        <h2 id="act-h">Activity</h2>
        <span className="note">{jobs === null ? "" : `${jobs.length} recent`}</span>
      </div>
      <div className="sum">
        <div>
          <p className="sum-cap">Today</p>
          <p className="sum-fig">
            <span className="sum-n">{today.length}</span> <span>{today.length === 1 ? "job" : "jobs"}</span>
          </p>
          <p className="sum-line">{today.length ? `${plural(steps, "step", "steps")}${failed ? `, ${plural(failed, "failed", "failed")}` : ", none failed"}.` : "Nothing finished yet today."}</p>
        </div>
      </div>
      <DayChart jobs={jobs ?? []} />
      {jobs?.length === 0 && <p className="act-empty">Work appears here as the agent does it.</p>}
      <ol className="list activity" ref={list}>
        {jobs?.map((job) => {
          const isOpen = open === job.job;
          const live = !job.ended;
          const bad = !!job.error || job.steps.some((s) => s.outcome === "failed");
          return (
            <li key={job.job} data-job={job.job} className={`${live ? "is-live" : ""}${bad ? " is-bad" : ""}${job.notice ? " is-notice" : ""}`}>
              <span className="kind" aria-hidden="true" />
              <span className="name">
                <button type="button" className="jobbtn" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : job.job)}>
                  {jobTitle(job)}
                </button>
              </span>
              <span className="when">{isToday(job.started) ? hhmm(job.started) : new Date(job.started).toLocaleDateString([], { day: "numeric", month: "short" })}</span>
              <span className="line">{jobLine(job)}</span>
              <span className="fig">{live ? <span className="badge">Working</span> : job.feedback ? (job.feedback.rating === "up" ? "Good" : "Not good") : ""}</span>
              {isOpen && (
                <div className="act-more">
                  {job.notice && job.message && <p className="act-note">{job.message}</p>}
                  {job.steps.length > 0 && (
                    <ol className="act-steps">
                      {job.steps.map((s, i) => {
                        const w = stepWords(asStep(s));
                        return (
                          <li key={i} className={`act-step act-step--${s.outcome}`}>
                            <span className="step-title">{w.title}</span>
                            <span className="step-cmd">{w.cmd}</span>
                            {w.why && <span className="step-why">{w.why}</span>}
                          </li>
                        );
                      })}
                    </ol>
                  )}
                  {job.reply && <p className="act-reply">{job.reply.length > REPLY_PREVIEW ? `${job.reply.slice(0, REPLY_PREVIEW)}…` : job.reply}</p>}
                  {job.error && <p className="ask-error">{job.error}</p>}
                  <Rate job={job} onRated={replace} />
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </>
  );
}
