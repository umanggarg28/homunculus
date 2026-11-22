/** Reading a memory page and changing it, by the rules in the memory's SCHEMA.md.
 *
 *  A page is markdown: a title and summary in frontmatter, one fact per line ending in
 *  footnotes that name raw sources, guesses under `## Inferred`, outdated facts kept
 *  and struck through with `~~`, and links under `## See also`. Every view reads a page
 *  through `parsePage`, and every change goes through the functions below, so the file
 *  stays the only state. */

export type ClaimKind = "fact" | "guess" | "old" | "mine";

export interface Claim {
  type: "claim";
  /** The line's index in the file, which the changes below act on. */
  line: number;
  /** The words, without the strike marks and footnotes. */
  text: string;
  /** Footnote names, such as "1", in the order the line uses them. */
  cites: string[];
  kind: ClaimKind;
  /** Lines in the same paragraph share a group. */
  group: number;
}

export interface Heading {
  type: "heading";
  line: number;
  text: string;
  group: number;
}

export type Item = Claim | Heading;

export interface ParsedPage {
  title: string;
  summary: string;
  items: Item[];
  /** Footnote name to the raw file it names. */
  footnotes: Record<string, string>;
  seeAlso: { title: string; href: string }[];
}

const FRONTMATTER = /^---\n([\s\S]*?)\n---\n/;
const FOOTNOTE_USE = /\[\^([^\]]+)\](?!:)/g;
const FOOTNOTE_DEFINITION = /^\[\^([^\]]+)\]:\s*(\S+)/;
const BULLET = /^(\s*(?:[-*+]|\d+\.)\s+)/;
const LINK = /\[([^\]]*)\]\(([^)\s]+)\)/g;

/** The frontmatter's fields, or none. */
export function frontmatter(text: string): Record<string, string> {
  const match = FRONTMATTER.exec(text);
  const fields: Record<string, string> = {};
  for (const line of match ? match[1].split("\n") : []) {
    const at = line.indexOf(":");
    if (at > 0) fields[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return fields;
}

function bodyStart(text: string): number {
  const match = FRONTMATTER.exec(text);
  return match ? match[0].split("\n").length - 1 : 0;
}

/** A line taken apart: its bullet, words, footnotes, and whether it is struck. */
function split(line: string): { bullet: string; text: string; cites: string[]; struck: boolean } {
  const bullet = BULLET.exec(line)?.[1] ?? "";
  let rest = line.slice(bullet.length).trim();
  const cites = [...rest.matchAll(FOOTNOTE_USE)].map((m) => m[1]);
  rest = rest.replace(FOOTNOTE_USE, "").trim();
  const struck = rest.startsWith("~~");
  if (struck) rest = rest.replace(/^~~/, "").replace(/~~$/, "").trim();
  return { bullet, text: rest, cites, struck };
}

export function parsePage(text: string): ParsedPage {
  const fields = frontmatter(text);
  const lines = text.split("\n");
  const items: Item[] = [];
  const footnotes: Record<string, string> = {};
  const seeAlso: ParsedPage["seeAlso"] = [];
  let section = "";
  let group = 0;
  let title = fields.title ?? "";
  for (let i = bodyStart(text); i < lines.length; i++) {
    const raw = lines[i];
    const stripped = raw.trim();
    if (!stripped) {
      group++;
      continue;
    }
    const definition = FOOTNOTE_DEFINITION.exec(stripped);
    if (definition) {
      footnotes[definition[1]] = definition[2];
      continue;
    }
    const heading = /^(#+)\s*(.*)$/.exec(stripped);
    if (heading) {
      // A top-level heading names the file, as "# Profile" does; it is not content.
      if (heading[1].length === 1) {
        title ||= heading[2];
        continue;
      }
      section = heading[2].trim();
      group++;
      if (section !== "Inferred" && section !== "See also") items.push({ type: "heading", line: i, text: section, group });
      continue;
    }
    if (section === "See also") {
      for (const link of stripped.matchAll(LINK)) seeAlso.push({ title: link[1], href: link[2] });
      continue;
    }
    const parts = split(raw);
    const kind: ClaimKind = parts.struck ? "old" : section === "Inferred" ? "guess" : parts.cites.length ? "fact" : "mine";
    items.push({ type: "claim", line: i, text: parts.text, cites: parts.cites, kind, group });
  }
  return { title, summary: fields.summary ?? "", items, footnotes, seeAlso };
}

/** One number per source on a page: current lines first, in the order they cite them,
 *  then sources only earlier lines use. Numbers never change with what is shown. */
export function numberSources(page: ParsedPage): string[] {
  const order: string[] = [];
  const claims = page.items.filter((item): item is Claim => item.type === "claim");
  for (const old of [false, true]) {
    for (const claim of claims) {
      if ((claim.kind === "old") !== old) continue;
      for (const name of claim.cites) {
        const path = page.footnotes[name];
        if (path && !order.includes(path)) order.push(path);
      }
    }
  }
  return order;
}

// Changes

/** Move a guess out of `## Inferred` to the end of the page's stated facts: the operator
 *  has confirmed it, so it is no longer a guess. */
export function confirmGuess(text: string, line: number): string {
  const lines = text.split("\n");
  const [moved] = lines.splice(line, 1);
  const inferred = lines.findIndex((l) => /^#+\s*Inferred\s*$/.test(l.trim()));
  if (inferred < 0) return text;
  let at = inferred;
  while (at > 0 && !lines[at - 1].trim()) at--;
  lines.splice(at, 0, moved);
  return tidy(lines);
}

/** Remove one line, such as a guess the operator says is wrong. */
export function removeLine(text: string, line: number): string {
  const lines = text.split("\n");
  lines.splice(line, 1);
  return tidy(lines);
}

/** Drop a section heading left with nothing under it, and runs of blank lines. */
function tidy(lines: string[]): string {
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const heading = /^#{2,}\s*Inferred\s*$/.test(lines[i].trim());
    if (heading) {
      let next = i + 1;
      while (next < lines.length && !lines[next].trim()) next++;
      if (next >= lines.length || /^#|^\[\^/.test(lines[next].trim())) continue;
    }
    if (!lines[i].trim() && out.length && !out[out.length - 1].trim()) continue;
    out.push(lines[i]);
  }
  return out.join("\n");
}

type Op = { op: "same" | "gone" | "new"; line: string };

/** The lines of `before` and `after` matched up: kept, removed, added. */
function diff(before: string[], after: string[]): Op[] {
  const n = before.length;
  const m = after.length;
  // lcs[i][j]: the longest common run of lines from before[i:] and after[j:].
  const lcs = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      lcs[i][j] = before[i] === after[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && before[i] === after[j]) {
      ops.push({ op: "same", line: before[i] });
      i++;
      j++;
    } else if (i < n && (j >= m || lcs[i + 1][j] >= lcs[i][j + 1])) {
      // A removal is placed before an addition at the same spot, so an old line reads
      // before the line that replaced it.
      ops.push({ op: "gone", line: before[i++] });
    } else {
      ops.push({ op: "new", line: after[j++] });
    }
  }
  return ops;
}

/** A sourced fact that is current: the kind of line whose history is kept. */
function isSourcedFact(line: string): boolean {
  const stripped = line.trim();
  if (!stripped || stripped.startsWith("#") || FOOTNOTE_DEFINITION.test(stripped)) return false;
  const parts = split(line);
  return parts.cites.length > 0 && !parts.struck;
}

function strike(line: string): string {
  const parts = split(line);
  return `${parts.bullet}~~${parts.text}~~${parts.cites.map((c) => `[^${c}]`).join("")}`;
}

export interface Saved {
  text: string;
  changed: number;
  added: number;
  removed: number;
}

/** The operator's edit as it is saved. The page keeps its own history:
 *  - a sourced fact they change or remove stays in the file, struck through, right above
 *    the line that replaced it;
 *  - an earlier, struck line stays even if it is deleted in the editor, because history
 *    is read only;
 *  - a line moved somewhere else unchanged is a move, not a change, and is not struck. */
export function withHistory(before: string, after: string): Saved {
  const ops = diff(before.split("\n"), after.split("\n"));
  const goneText = new Set(ops.filter((o) => o.op === "gone").map((o) => o.line));
  const newText = new Set(ops.filter((o) => o.op === "new").map((o) => o.line));
  const moved = (line: string) => line.trim() !== "" && goneText.has(line) && newText.has(line);
  // In the history the frontmatter belongs to the page, never to a fact.
  const frontmatterLines = bodyStart(before);
  const out: string[] = [];
  const saved: Saved = { text: "", changed: 0, added: 0, removed: 0 };
  let gone: { line: string; index: number }[] = [];
  let fresh: string[] = [];

  /** Write out one stretch of removed and added lines, between lines that stayed. */
  const flush = () => {
    let next = 0;
    let removedHere = 0;
    for (const { line, index } of gone) {
      if (moved(line)) continue;
      const parts = split(line);
      if (index >= frontmatterLines && parts.struck) {
        out.push(line);
        continue;
      }
      if (line.trim()) removedHere++;
      if (index >= frontmatterLines && isSourcedFact(line)) out.push(strike(line));
      if (next < fresh.length) out.push(fresh[next++]);
    }
    out.push(...fresh.slice(next));
    const addedHere = fresh.filter((line) => line.trim() && !moved(line)).length;
    const pairs = Math.min(removedHere, addedHere);
    saved.changed += pairs;
    saved.added += addedHere - pairs;
    saved.removed += removedHere - pairs;
    gone = [];
    fresh = [];
  };

  let index = 0;
  for (const op of ops) {
    if (op.op === "same") {
      flush();
      out.push(op.line);
      index++;
    } else if (op.op === "gone") {
      gone.push({ line: op.line, index: index++ });
    } else {
      fresh.push(op.line);
    }
  }
  flush();
  saved.text = out.join("\n");
  return saved;
}

/** "1 line changed, 1 line added" */
export function describeSave(saved: Saved): string {
  const part = (n: number, what: string) => (n ? `${n} line${n === 1 ? "" : "s"} ${what}` : "");
  return [part(saved.changed, "changed"), part(saved.added, "added"), part(saved.removed, "removed")].filter(Boolean).join(", ");
}
