/** Minimal line diff (LCS) for the proposal review surface.
 *
 *  Skill files are ~100 lines, so the O(n·m) table is nothing — and a
 *  dependency-free 60-line implementation beats shipping a diff library
 *  for one screen. Unchanged runs longer than `keep·2 + 1` collapse to a
 *  gap marker so a one-line edit reads as one line, not a wall of text.
 */

export type DiffLine =
  | { type: "ctx" | "add" | "del"; text: string }
  | { type: "gap"; hidden: number };

export function diffLines(before: string, after: string, keep = 2): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");

  // LCS length table.
  const m = a.length, n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  // Walk the table into a raw op list.
  const raw: Exclude<DiffLine, { type: "gap"; hidden: number }>[] = [];
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      raw.push({ type: "ctx", text: a[i] });
      i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      raw.push({ type: "del", text: a[i] });
      i++;
    } else {
      raw.push({ type: "add", text: b[j] });
      j++;
    }
  }
  while (i < m) raw.push({ type: "del", text: a[i++] });
  while (j < n) raw.push({ type: "add", text: b[j++] });

  // Collapse long unchanged runs, keeping `keep` context lines on each side.
  const out: DiffLine[] = [];
  let run: typeof raw = [];
  const flushRun = (isLast: boolean, isFirst: boolean) => {
    const head = isFirst ? 0 : keep;
    const tail = isLast ? 0 : keep;
    if (run.length > head + tail + 1) {
      if (head) out.push(...run.slice(0, head));
      out.push({ type: "gap", hidden: run.length - head - tail });
      if (tail) out.push(...run.slice(run.length - tail));
    } else {
      out.push(...run);
    }
    run = [];
  };
  let sawChange = false;
  for (const op of raw) {
    if (op.type === "ctx") {
      run.push(op);
    } else {
      flushRun(false, !sawChange);
      sawChange = true;
      out.push(op);
    }
  }
  flushRun(true, !sawChange);
  return out;
}

export function diffStats(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0, removed = 0;
  for (const l of lines) {
    if (l.type === "add") added++;
    else if (l.type === "del") removed++;
  }
  return { added, removed };
}
