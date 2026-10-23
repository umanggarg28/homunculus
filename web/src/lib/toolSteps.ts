/** One tool step of a reply, as the chat stream reported it. */
export interface ToolStep {
  name: string;
  /** The gate's answer: "act" when the tool ran, otherwise why it did not. */
  answer: string;
  arguments: string;
  result: string;
  durationMs: number;
}

/** Gate answers that leave an action waiting for the operator. */
const WAITING_ANSWERS = new Set(["ask", "hand off", "act when asked"]);

export type StepOutcome = "ok" | "waiting" | "failed";

/** How a step ended: it ran, it is waiting for the operator, or it failed (refused,
 *  or the tool reported an error). Every part of the console uses this one rule,
 *  so they always agree. */
export function stepOutcome(answer: string | undefined, result: string | undefined): StepOutcome {
  if (answer && WAITING_ANSWERS.has(answer)) return "waiting";
  if (answer !== "act" || /^error/i.test(result ?? "")) return "failed";
  return "ok";
}

/** A tool's arguments as (name, value) pairs, for display. Arguments that are not a
 *  JSON object are shown as one raw value. */
export function argumentPairs(raw: string | undefined): Array<[string, string]> {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return Object.entries(parsed).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]);
    }
  } catch {
    // not JSON: shown as written
  }
  const collapsed = raw.replace(/\s+/g, " ").trim();
  return collapsed ? [["raw", collapsed]] : [];
}
