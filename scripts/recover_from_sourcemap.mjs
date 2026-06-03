#!/usr/bin/env node
/**
 * Extract original TSX/CSS sources from a Vite-built source map.
 *
 * Source maps embed every source file as a string in `sourcesContent`,
 * paired with the original path in `sources`. This script reads a
 * .js.map file and writes each referenced source to a chosen output
 * dir for inspection / selective recovery.
 *
 * Usage:
 *   node recover_from_sourcemap.mjs <map-file> <out-dir> [filter-substring]
 *
 * Example:
 *   node recover_from_sourcemap.mjs TasksPage.js.map /tmp/recovered TaskRow
 */
import fs from "node:fs";
import path from "node:path";

const [, , mapPath, outDir, filter = ""] = process.argv;
if (!mapPath || !outDir) {
  console.error("usage: recover_from_sourcemap.mjs <map> <outdir> [filter]");
  process.exit(2);
}

const m = JSON.parse(fs.readFileSync(mapPath, "utf8"));
const sources = m.sources ?? [];
const contents = m.sourcesContent ?? [];

let written = 0;
for (let i = 0; i < sources.length; i++) {
  const src = sources[i];
  const body = contents[i];
  if (!src || body == null) continue;
  if (filter && !src.includes(filter)) continue;
  // Sources look like ../../../src/components/tasks/TaskRow.tsx —
  // strip leading ../ and rebuild under outDir.
  const cleaned = src.replace(/^(\.\.\/)+/, "");
  const dest = path.join(outDir, cleaned);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, body, "utf8");
  console.log("wrote", dest, `(${body.length} bytes)`);
  written++;
}
console.log(`\n${written} file(s) recovered from ${mapPath}`);
