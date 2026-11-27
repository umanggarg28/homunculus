// The server labels content from outside, such as a web page, with a tag on a line of
// its own before and after it. The agent reads the label; a person does not need it.
const LABEL = /^<\/?untrusted_content\b[^>]*>$/;

/** A tool's result as a person reads it: without the lines that only label outside content. */
export function withoutLabel(text: string): string {
  return text
    .split("\n")
    .filter((line) => !LABEL.test(line.trim()))
    .join("\n");
}

/** The first line of a result with something in it. */
export function firstWords(text: string): string {
  return withoutLabel(text).split("\n").find((line) => line.trim()) ?? "";
}
