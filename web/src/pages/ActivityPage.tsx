import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { api, type ActivityStep, type Job, type Rating } from "@/lib/api";
import { useTurnEvents } from "@/lib/turnEvents";

// The agent can work while the operator is on another screen, so the timeline is
// refreshed on a timer as well as after every turn event.
const REFRESH_MS = 10_000;
const REPLY_PREVIEW = 280;
// Only whether a turn event arrived matters here, not the events themselves.
const LATEST_EVENT_ONLY = 1;

/** What the agent did, one job per turn, newest first, read from the activity log.
 *  Each job can be rated, so the agent can learn what good work looks like. */
export function ActivityPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const { events } = useTurnEvents(LATEST_EVENT_ONLY);

  const refresh = useCallback(async () => {
    try {
      setJobs(await api.activity());
    } catch {
      // Unreachable server: keep the last timeline rather than showing an empty one.
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

  const replace = (updated: Job) =>
    setJobs((current) => current?.map((j) => (j.job === updated.job ? updated : j)) ?? null);

  return (
    <PageShell>
      <PageHeader
        title="Activity"
        subtitle={jobs === null ? "loading" : `${jobs.length} recent job${jobs.length === 1 ? "" : "s"}`}
      />
      <div className="max-w-[720px] mx-auto flex flex-col gap-4" style={{ paddingBottom: 48 }}>
        {jobs?.length === 0 && (
          <div
            className="text-[11px] uppercase tracking-[0.18em] py-10 text-center"
            style={{ color: "var(--color-text-faint)" }}
          >
            nothing yet: work appears here as the agent does it
          </div>
        )}
        {jobs?.map((job) => <JobCard key={job.job} job={job} onRated={replace} />)}
      </div>
    </PageShell>
  );
}

function JobCard({ job, onRated }: { job: Job; onRated: (job: Job) => void }) {
  const ended = job.ended !== null;
  return (
    <div data-testid={`job-${job.job}`} className="p-4" style={{ border: "1px solid var(--color-border)" }}>
      <div className="flex items-baseline justify-between gap-4 text-[10px] uppercase tracking-[0.14em]">
        <span style={{ color: job.notice ? "var(--color-warning)" : "var(--color-accent)" }}>
          {job.notice ? "decision" : "you"}
        </span>
        <span style={{ color: "var(--color-text-faint)" }}>
          {formatTime(job.started)}
          {ended ? ` · ${formatDuration(job.started, job.ended!)}` : " · not finished"}
        </span>
      </div>
      <div
        className="mt-1 text-[12.5px]"
        style={{
          color: job.notice ? "var(--color-text-muted)" : "var(--color-text)",
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
        }}
      >
        {job.message}
      </div>

      {job.steps.length > 0 && (
        <div className="mt-3 flex flex-col gap-1 pl-3" style={{ borderLeft: "1px solid var(--color-border-strong)" }}>
          {job.steps.map((step, i) => (
            <StepRow key={i} step={step} />
          ))}
        </div>
      )}

      {job.reply && (
        <div className="mt-3 text-[12px]" style={{ color: "var(--color-text-dim)", whiteSpace: "pre-wrap" }}>
          › {job.reply.length > REPLY_PREVIEW ? `${job.reply.slice(0, REPLY_PREVIEW)}…` : job.reply}
        </div>
      )}
      {job.error && (
        <div className="mt-3 text-[11px]" style={{ color: "var(--color-danger)" }}>
          error: {job.error}
        </div>
      )}

      <RateJob job={job} onRated={onRated} />
    </div>
  );
}

const OUTCOME_STYLE = {
  ok: { glyph: "✓", color: "var(--color-accent)" },
  waiting: { glyph: "◷", color: "var(--color-warning)" },
  failed: { glyph: "✗", color: "var(--color-danger)" },
} as const;

function StepRow({ step }: { step: ActivityStep }) {
  // An outcome this console does not know is shown as unknown, not guessed.
  const style = OUTCOME_STYLE[step.outcome] ?? { glyph: "?", color: "var(--color-text-faint)" };
  const label = step.answer === "act" ? (step.request ? "approved" : null) : step.answer;
  return (
    <div className="text-[11.5px] flex gap-2 min-w-0">
      <span style={{ color: style.color }}>{style.glyph}</span>
      <span style={{ color: "var(--color-text)" }}>{step.name}</span>
      <span className="truncate" style={{ color: "var(--color-text-muted)" }} title={step.result}>
        {label ? `${label}: ${step.result}` : step.result}
      </span>
    </div>
  );
}

const PROMPT: Record<Rating, string> = {
  up: "what was good? (optional)",
  down: "what could be better? (optional)",
};

/** Thumbs up or down, each with an optional reason. A later rating replaces an earlier one. */
function RateJob({ job, onRated }: { job: Job; onRated: (job: Job) => void }) {
  const [choosing, setChoosing] = useState<Rating | null>(null);
  // Each new rating starts with no reason, so an old one is never sent with it.
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const current = job.feedback?.rating ?? null;

  const choose = (rating: Rating) => {
    setChoosing(rating);
    setReason("");
    setError(null);
  };

  const save = async (rating: Rating) => {
    setError(null);
    try {
      onRated(await api.feedback(job.job, rating, reason.trim() || undefined));
      setChoosing(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const thumb = (rating: Rating, glyph: string) => {
    const marked = (choosing ?? current) === rating;
    return (
      <button
        aria-label={rating === "up" ? "good work" : "not good work"}
        aria-pressed={current === rating}
        onClick={() => choose(rating)}
        className="text-[12px] px-2 py-[2px]"
        style={{
          cursor: "pointer",
          background: marked ? "var(--color-accent-dim)" : "transparent",
          border: `1px solid ${marked ? "var(--color-accent)" : "var(--color-border)"}`,
          color: "var(--color-text)",
        }}
      >
        {glyph}
      </button>
    );
  };

  return (
    <div className="mt-3 flex flex-col gap-2">
      <div className="flex items-center gap-2">
        {thumb("up", "👍")}
        {thumb("down", "👎")}
        {job.feedback?.reason && choosing === null && (
          <span className="text-[11px]" style={{ color: "var(--color-text-muted)" }}>
            “{job.feedback.reason}”
          </span>
        )}
      </div>
      {choosing && (
        <div className="flex gap-2">
          <input
            autoFocus
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save(choosing);
              if (e.key === "Escape") setChoosing(null);
            }}
            placeholder={PROMPT[choosing]}
            className="flex-1 px-2 py-1 text-[12px]"
            style={{
              background: "var(--color-bg)",
              border: "1px solid var(--color-border)",
              color: "var(--color-text)",
              fontFamily: "var(--font-mono)",
            }}
          />
          <button
            onClick={() => save(choosing)}
            className="text-[11px] uppercase tracking-[0.16em] px-3"
            style={{ cursor: "pointer", border: "1px solid var(--color-accent)", color: "var(--color-accent)", background: "transparent" }}
          >
            [save]
          </button>
        </div>
      )}
      {error && (
        <div className="text-[11px]" style={{ color: "var(--color-danger)" }}>
          {error}
        </div>
      )}
    </div>
  );
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--";
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

function formatDuration(started: string, ended: string): string {
  const seconds = Math.max(0, (new Date(ended).getTime() - new Date(started).getTime()) / 1000);
  return seconds < 60 ? `${seconds.toFixed(1)}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}
