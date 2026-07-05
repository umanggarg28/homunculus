import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { api } from "@/lib/api";

interface Alert {
  level: "danger" | "warn";
  text: string;
}

// "check traces" alerts (tool errors, stuck loops) are acknowledged by
// actually opening Traces. We record when that last happened and hide
// those event-based alerts older than it. Live conditions (budget over,
// task still failing) are NOT cleared this way — that would hide a
// still-true state.
const TRACES_SEEN_KEY = "hm.tracesSeenAt";
export const TRACES_SEEN_EVENT = "hm:traces-seen";

/** Call when the Traces page is opened — clears the "check traces" alerts. */
export function markTracesSeen(): void {
  const t = Date.now();
  localStorage.setItem(TRACES_SEEN_KEY, String(t));
  window.dispatchEvent(new CustomEvent(TRACES_SEEN_EVENT, { detail: t }));
}

// Same acknowledgment contract for the task-failure alert: it says
// "check task detail", so opening Tasks (where the RETRY badge and the
// failing run are in view) IS the check. Only a run that fails AFTER
// that visit re-lights the banner — an alert that nags all day about a
// failure already inspected trains the reader to ignore the strip.
const TASKS_SEEN_KEY = "hm.tasksSeenAt";
export const TASKS_SEEN_EVENT = "hm:tasks-seen";

/** Call when the Tasks page is opened — acknowledges current task-failure alerts. */
export function markTasksSeen(): void {
  const t = Date.now();
  localStorage.setItem(TASKS_SEEN_KEY, String(t));
  window.dispatchEvent(new CustomEvent(TASKS_SEEN_EVENT, { detail: t }));
}


// Tool errors where the AGENT mis-called the tool — bad/missing arguments,
// an invalid regex, a target that doesn't exist — then immediately retries.
// These are self-correcting, not infrastructure failures, so they must not
// raise the "check traces" alarm (they still appear in Traces). Genuine
// failures — HTTP errors, timeouts, provider/network trouble — do NOT match
// here and still alert.
const RECOVERABLE_TOOL_ERROR = [
  "invalid arguments for '",
  "invalid regex '",
  "to record a failure against",
  "does not exist",
];
function isRecoverableToolError(result: string): boolean {
  const s = result.toLowerCase();
  return RECOVERABLE_TOOL_ERROR.some((p) => s.includes(p));
}

/** Contextual alert strip — surfaces hard failures (recent tool
 *  errors, missing API keys, failed task runs, stuck loops, budget overrun)
 *  at the top of every page. Hairline border in danger/amber, hides itself when clean. */
export function AlertBanner() {
  const { events } = useEventStream(200);
  const [tasksFailed, setTasksFailed] = useState(0);
  const [budgetCents, setBudgetCents] = useState(0);
  const [spentCents, setSpentCents] = useState(0);
  const [tracesSeenAt, setTracesSeenAt] = useState(
    () => Number(localStorage.getItem(TRACES_SEEN_KEY)) || 0,
  );
  const [tasksSeenAt, setTasksSeenAt] = useState(
    () => Number(localStorage.getItem(TASKS_SEEN_KEY)) || 0,
  );

  useEffect(() => {
    const onSeen = (e: Event) =>
      setTracesSeenAt((e as CustomEvent).detail ?? Date.now());
    const onTasksSeen = (e: Event) =>
      setTasksSeenAt((e as CustomEvent).detail ?? Date.now());
    window.addEventListener(TRACES_SEEN_EVENT, onSeen);
    window.addEventListener(TASKS_SEEN_EVENT, onTasksSeen);
    return () => {
      window.removeEventListener(TRACES_SEEN_EVENT, onSeen);
      window.removeEventListener(TASKS_SEEN_EVENT, onTasksSeen);
    };
  }, []);

  useEffect(() => {
    api.tasksList("all").then((tasks) => {
      const startOfToday = (() => { const d = new Date(); d.setHours(0,0,0,0); return d.getTime(); })();
      // Failures older than the last Tasks visit are acknowledged —
      // only runs that failed since then count toward the banner.
      const cutoff = Math.max(startOfToday, tasksSeenAt);
      let failed = 0;
      for (const t of tasks) {
        for (const r of (t.last_runs || [])) {
          // Provider exhaustion is a transient infra failure, not a broken task — exclude it
          const isProviderExhaustion = typeof r.result === "string" && r.result.toLowerCase().includes("all providers exhausted");
          if (r.status === "failure" && !isProviderExhaustion && new Date(r.ts).getTime() >= cutoff) failed += 1;
        }
      }
      setTasksFailed(failed);
    }).catch(() => undefined);
  }, [events.length, tasksSeenAt]);

  useEffect(() => {
    api.statsToday().then((s) => {
      setBudgetCents(s.budget_cents ?? 0);
      setSpentCents(s.cost_cents ?? 0);
    }).catch(() => undefined);
  }, [events.length]);

  const alerts = useMemo<Alert[]>(() => {
    const out: Alert[] = [];
    const cutoff5m = Date.now() - 5 * 60 * 1000;
    // "check traces" alerts clear once Traces has been opened: only show
    // events newer than both the 5-min window and the last Traces visit.
    const ackCutoff = Math.max(cutoff5m, tracesSeenAt);

    // Recent tool failures
    const recentErr = events.filter(
      (e) =>
        e.event === "tool_result" &&
        typeof e.result === "string" &&
        e.result.startsWith("ERROR") &&
        !isRecoverableToolError(e.result) &&
        new Date(e.ts).getTime() > ackCutoff,
    );
    if (recentErr.length > 0) {
      out.push({
        level: "danger",
        text: `${recentErr.length} tool error${recentErr.length > 1 ? "s" : ""} in the last 5 min · check traces`,
      });
    }

    // Stuck-loop detection
    const recentLoops = events.filter(
      (e) =>
        e.event === "output_guard" &&
        typeof e.text === "string" &&
        e.text.startsWith("stuck loop") &&
        new Date(e.ts).getTime() > ackCutoff,
    );
    if (recentLoops.length > 0) {
      const last = recentLoops[recentLoops.length - 1];
      out.push({
        level: "danger",
        text: `agent stuck: ${last.name ?? "tool"} called repeatedly · check traces`,
      });
    }

    if (tasksFailed > 0) {
      out.push({
        level: "warn",
        text: `${tasksFailed} task run${tasksFailed > 1 ? "s" : ""} failed today · check task detail`,
      });
    }

    // Budget guard
    if (budgetCents > 0 && spentCents > 0) {
      const pct = spentCents / budgetCents;
      if (pct >= 1) {
        const costStr = spentCents >= 100 ? `$${(spentCents / 100).toFixed(2)}` : `¢${spentCents.toFixed(1)}`;
        const capStr = budgetCents >= 100 ? `$${(budgetCents / 100).toFixed(2)}` : `¢${budgetCents.toFixed(1)}`;
        out.push({ level: "danger", text: `daily budget exceeded: ${costStr} spent of ${capStr} cap` });
      } else if (pct >= 0.8) {
        const pctStr = Math.round(pct * 100);
        out.push({ level: "warn", text: `${pctStr}% of daily budget used` });
      }
    }

    return out;
  }, [events, tasksFailed, budgetCents, spentCents, tracesSeenAt]);

  // The banner and the sticky PageHeader share the viewport top edge —
  // publish the banner's measured height so the header sticks BELOW it
  // (PageHeader reads --hm-alert-offset for its `top`). Measured, not
  // hardcoded: the strip stacks one row per active alert.
  const wrapRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const root = document.documentElement;
    const el = wrapRef.current;
    if (!el || alerts.length === 0) {
      root.style.setProperty("--hm-alert-offset", "0px");
      return;
    }
    const update = () => root.style.setProperty("--hm-alert-offset", `${el.offsetHeight}px`);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.setProperty("--hm-alert-offset", "0px");
    };
  }, [alerts.length]);

  if (alerts.length === 0) return null;

  return (
    <div ref={wrapRef} style={{ position: "sticky", top: 0, zIndex: 70, fontFamily: "var(--font-mono)" }}>
      {alerts.map((a, i) => (
        <div
          key={i}
          className="px-10 py-2 flex items-center gap-3 text-[10px] uppercase tracking-[0.14em]"
          style={{
            background: "var(--color-bg)",
            borderBottom: `1px solid ${a.level === "danger" ? "var(--color-danger)" : "var(--color-amber)"}`,
            color: "var(--color-text-dim)",
          }}
        >
          <span style={{ color: a.level === "danger" ? "var(--color-danger)" : "var(--color-amber)" }}>
            ● {a.level === "danger" ? "alert" : "warn"}
          </span>
          <span style={{ color: "var(--color-border-strong)" }}>──</span>
          <span>{a.text}</span>
        </div>
      ))}
    </div>
  );
}
