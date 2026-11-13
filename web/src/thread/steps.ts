import { argumentPairs, type ToolStep } from "@/lib/toolSteps";

/** How one tool step reads on its card: a title in words, the command or tool call it
 *  was in mono, and, when it did not simply run, why. All of it is taken from the step
 *  the server sent; nothing is summarised by guessing. */
export interface StepWords {
  title: string;
  cmd: string;
  why: string | null;
}

const MAX = 72;
const clip = (s: string, n = MAX) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** The value a step acts on: a path, a command or a search, else the first argument. */
export function stepSubject(args: string): string | null {
  const pairs = argumentPairs(args);
  const by = Object.fromEntries(pairs);
  return by.path ?? by.command ?? by.query ?? by.pattern ?? pairs[0]?.[1] ?? null;
}

const DONE: Record<string, (s: string | null) => string> = {
  read: (s) => (s ? `Read ${s}` : "Read a file"),
  write: (s) => (s ? `Wrote ${s}` : "Wrote a file"),
  edit: (s) => (s ? `Edited ${s}` : "Edited a file"),
  list: (s) => (s && s !== "." ? `Looked in ${s}` : "Looked through the files"),
  search: (s) => (s ? `Searched for ${s}` : "Searched the files"),
  delete: (s) => (s ? `Deleted ${s}` : "Deleted a file"),
  rename: (s) => (s ? `Renamed ${s}` : "Renamed a file"),
  shell: () => "Ran a command",
};

// The first line of a result, which is where tools put what happened.
const firstLine = (text: string) => text.split("\n").find((l) => l.trim())?.trim() ?? "";

export function stepWords(step: ToolStep): StepWords {
  const subject = stepSubject(step.arguments);
  const cmd = clip(step.name === "shell" && subject ? subject : [step.name, subject].filter(Boolean).join(" "));
  if (step.outcome === "waiting") {
    return { title: "Needs your approval", cmd, why: step.answer === "hand off" ? "Only you can do this. It is waiting for you." : "It asked before running this." };
  }
  const short = subject ? clip(subject, 40) : null;
  const title = DONE[step.name]?.(short) ?? `Used ${step.name}`;
  if (step.outcome === "failed") {
    const refused = step.answer !== "act";
    return { title: refused ? `Not allowed: ${step.name}` : `${title}, failed`, cmd, why: clip(firstLine(step.result) || step.answer, 140) };
  }
  return { title, cmd, why: null };
}

/** A step's duration the way the card shows it. */
export function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

/** The card's header tag: where the work is, or how it ended. */
export function workTag(steps: ToolStep[], running: boolean): { tone: "now" | "ok" | "bad" | "wait"; text: string } {
  const failed = steps.filter((s) => s.outcome === "failed").length;
  if (running) return { tone: "now", text: `Step ${steps.length}` };
  if (steps[steps.length - 1]?.outcome === "waiting") return { tone: "wait", text: "Waiting on you" };
  if (failed) return { tone: "bad", text: failed === 1 ? `${steps.length} steps, one failed` : `${steps.length} steps, ${failed} failed` };
  return { tone: "ok", text: steps.length === 1 ? "One step" : `${steps.length} steps` };
}
