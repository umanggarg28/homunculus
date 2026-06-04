import { Fragment, useMemo } from "react";
import { Link } from "react-router-dom";
import type { MemoryEntry } from "@/lib/types";

interface Props {
  text: string;
  entries: MemoryEntry[];
}

// [[some-name]] wikilink pattern. Slugs are kebab-case ASCII per the
// memory convention; this regex stays narrow on purpose so prose like
// "[[note: see X]]" doesn't accidentally render as a link.
const WIKILINK = /\[\[([a-z0-9][a-z0-9\-_]*)\]\]/gi;

/** Renders memory file contents with [[name]] cross-links resolved to
 *  the matching memory entry by its frontmatter `name:` field. Unknown
 *  names render as a dim "broken link" stub so the author notices.
 *  Everything else is unchanged from the original <pre> view.
 */
export function MemoryContent({ text, entries }: Props) {
  const byName = useMemo(() => {
    const m = new Map<string, MemoryEntry>();
    for (const e of entries) m.set(e.name.toLowerCase(), e);
    return m;
  }, [entries]);

  const parts = useMemo(() => splitOnWikilinks(text), [text]);

  return (
    <pre
      className="whitespace-pre-wrap break-words"
      style={{
        border: "1px solid var(--color-border)",
        background: "var(--color-surface-1)",
        color: "var(--color-text)",
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        lineHeight: 1.65,
        padding: "16px 18px",
        margin: 0,
      }}
    >
      {parts.map((p, i) => {
        if (p.type === "text") return <Fragment key={i}>{p.value}</Fragment>;
        const hit = byName.get(p.name.toLowerCase());
        if (hit) {
          return (
            <Link
              key={i}
              to={`/memory/${encodeURIComponent(hit.filename)}`}
              style={{
                color: "var(--color-accent)",
                textDecoration: "underline",
                textUnderlineOffset: 2,
              }}
              title={hit.description || hit.filename}
            >
              [[{p.name}]]
            </Link>
          );
        }
        return (
          <span
            key={i}
            title="no matching memory — link is dangling"
            style={{
              color: "var(--color-text-faint)",
              fontStyle: "italic",
              textDecoration: "underline dotted",
              textUnderlineOffset: 2,
            }}
          >
            [[{p.name}]]
          </span>
        );
      })}
    </pre>
  );
}

type Part = { type: "text"; value: string } | { type: "link"; name: string };

function splitOnWikilinks(text: string): Part[] {
  const out: Part[] = [];
  let last = 0;
  for (const m of text.matchAll(WIKILINK)) {
    const start = m.index ?? 0;
    if (start > last) out.push({ type: "text", value: text.slice(last, start) });
    out.push({ type: "link", name: m[1] });
    last = start + m[0].length;
  }
  if (last < text.length) out.push({ type: "text", value: text.slice(last) });
  return out;
}
