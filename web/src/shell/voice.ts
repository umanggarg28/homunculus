import type { AgentState } from "@/state/agentState";
import { argumentPairs } from "@/lib/toolSteps";

/** What the agent says about the moment, as a spoken line, a quieter line under it,
 *  and the status pill's words. Every part comes from the agent's state or the
 *  activity log; nothing here is a guess. */
export interface Voice {
  say: string;
  meta: string;
  pill: string;
}

const hhmm = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "";

export function greeting(hour: number): string {
  if (hour < 5) return "Good evening.";
  if (hour < 12) return "Good morning.";
  if (hour < 18) return "Good afternoon.";
  return "Good evening.";
}

export function jobsToday(count: number | null): string {
  if (count === null) return "";
  if (count === 0) return "No jobs yet today.";
  if (count === 1) return "One job done today.";
  return `${count} jobs done today.`;
}

// The file or command a step acts on, so the line can name it.
function subject(args: string | undefined): string | null {
  const pairs = Object.fromEntries(argumentPairs(args));
  const value = pairs.path ?? pairs.command ?? pairs.query ?? pairs.raw ?? null;
  if (!value) return null;
  return value.length > 48 ? `${value.slice(0, 47)}…` : value;
}

const DOING: Record<string, (s: string | null) => string> = {
  read: (s) => (s ? `Reading ${s}.` : "Reading a file."),
  write: (s) => (s ? `Writing ${s}.` : "Writing a file."),
  edit: (s) => (s ? `Editing ${s}.` : "Editing a file."),
  list: (s) => (s && s !== "." ? `Looking in ${s}.` : "Looking through the files."),
  search: (s) => (s ? `Searching for ${s}.` : "Searching the files."),
  delete: (s) => (s ? `Deleting ${s}.` : "Deleting a file."),
  rename: (s) => (s ? `Renaming ${s}.` : "Renaming a file."),
  shell: (s) => (s ? `Running ${s}.` : "Running a command."),
};

// The first sentence of an error, short enough to read at a glance.
function firstSentence(text: string | null): string {
  if (!text) return "";
  const one = text.split(/(?<=\.)\s/)[0];
  return one.length > 90 ? `${one.slice(0, 89)}…` : one;
}

export function voiceOf(state: AgentState, doneToday: number | null, now: Date): Voice {
  switch (state.kind) {
    case "thinking":
      return { say: "Thinking it over.", meta: state.since ? `Since ${hhmm(state.since)}` : "", pill: "Thinking" };
    case "working": {
      const name = state.last?.name ?? "";
      const line = DOING[name]?.(subject(state.last?.arguments)) ?? `Using ${name}.`;
      return { say: line, meta: `Step ${state.step}, ${name}`, pill: `Working, step ${state.step}` };
    }
    case "waiting":
      return {
        say: state.pending === 1 ? "One thing needs your approval." : `${state.pending} things need your approval.`,
        meta: state.pendingSince ? `Waiting since ${hhmm(state.pendingSince)}` : "",
        pill: "Needs approval",
      };
    case "done":
      return { say: "Done.", meta: jobsToday(doneToday), pill: "Ready" };
    case "stuck":
      return { say: "Something went wrong.", meta: firstSentence(state.error), pill: "Stopped" };
    case "stopped":
      return { say: "You stopped this job.", meta: "Steps that already ran are not undone.", pill: "Stopped" };
    default:
      return { say: greeting(now.getHours()), meta: jobsToday(doneToday), pill: "Ready" };
  }
}
