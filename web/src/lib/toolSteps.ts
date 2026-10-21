/** One tool step of a reply, as the chat stream reported it. */
export interface ToolStep {
  name: string;
  /** The gate's answer: "act" when the tool ran, otherwise why it did not. */
  answer: string;
  arguments: string;
  result: string;
  durationMs: number;
}

/** A step failed when the gate did not let it run, or the tool reported an error.
 *  Every part of the console uses this one rule, so they always agree. */
export function isFailedStep(answer: string | undefined, result: string | undefined): boolean {
  return answer !== "act" || /^error/i.test(result ?? "");
}
