import { useEffect, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "react-router-dom";
import { StaleError, memoryApi, type MemoryLogEntry, type MemoryPage, type MemoryPageInfo } from "@/lib/api";
import { numberSources, parsePage, type Claim, type ParsedPage } from "./wiki";

// Addresses

export const pageHref = (path: string) => `/memory/${path}`;
export const sourceHref = (path: string) => `/memory/${path}`;

/** A link inside a page, relative to the page's own folder, as a path in memory. */
function resolve(from: string, href: string): string | null {
  if (/^[a-z]+:\/\//i.test(href) || href.startsWith("#")) return null;
  const parts = from.split("/").slice(0, -1);
  for (const part of href.split("#")[0].split("/")) {
    if (part === "..") parts.pop();
    else if (part && part !== ".") parts.push(part);
  }
  return parts.join("/");
}

/** A claim's words, with their markdown, and links to other pages kept inside the app. */
function Words({ text, from }: { text: string; from: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <>{children}</>,
        a: ({ href, children }) => {
          const inside = href ? resolve(from, href) : null;
          return inside ? (
            <Link className="wl" to={pageHref(inside)}>
              {children}
            </Link>
          ) : (
            <a className="wl" href={href} rel="noreferrer noopener" target="_blank">
              {children}
            </a>
          );
        },
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

// The page list

export function PageList({ pages, current, drafts }: { pages: MemoryPageInfo[]; current: string; drafts: Set<string> }) {
  const guesses = pages.reduce((n, p) => n + p.guesses, 0);
  const row = (key: string, to: string, name: string, line: string, badge: number, label?: string) => (
    <li key={key} className={current === key ? "is-sel" : undefined}>
      <Link className="mem-link" to={to} aria-current={current === key ? "page" : undefined} data-key={key}>
        <span className="name">{name}</span>
        {badge > 0 && (
          <span className="badge-attn" aria-label={`${badge} guess${badge === 1 ? "" : "es"} to check`}>
            {badge}
          </span>
        )}
        <span className="line">{label ?? line}</span>
      </Link>
    </li>
  );
  return (
    <nav className="pg-rail panel" aria-label="Memory pages">
      <div className="panel-head">
        <h2>Memory</h2>
      </div>
      <ul className="list mem-nav">
        {row("about", "/memory/about", "About you", "What I know, and where it came from.", guesses)}
        {pages.map((p) =>
          row(p.path, pageHref(p.path), p.title, p.summary, p.guesses, drafts.has(p.path) ? "Unsaved changes" : undefined),
        )}
      </ul>
      {pages.length === 0 && <p className="pg-foot">No pages yet. I write one when I learn something about you.</p>}
    </nav>
  );
}

// Claims

export interface ClaimActions {
  confirm?: (claim: Claim) => void;
  remove?: (claim: Claim) => void;
}

/** A page's lines in short paragraphs, as the file groups them, each fact with its
 *  numbered sources. Earlier lines are shown only on request. */
export function Claims({
  page,
  path,
  numbers,
  showOld,
  chosen,
  onCite,
  actions,
}: {
  page: ParsedPage;
  path: string;
  numbers: string[];
  showOld: boolean;
  chosen: string | null;
  onCite?: (source: string) => void;
  actions?: ClaimActions;
}) {
  const groups = new Map<number, ReactNode[]>();
  for (const item of page.items) {
    if (item.type === "claim" && item.kind === "old" && !showOld) continue;
    const list = groups.get(item.group) ?? [];
    if (item.type === "heading") {
      list.push(
        <h3 key={item.line} className="cl-h">
          {item.text}
        </h3>,
      );
    } else {
      list.push(<ClaimLine key={item.line} claim={item} page={page} path={path} numbers={numbers} chosen={chosen} onCite={onCite} actions={actions} />);
    }
    groups.set(item.group, list);
  }
  return (
    <div className="wiki">
      {[...groups.entries()].map(([group, lines]) => (
        <div className="cl-group" key={group}>
          {lines}
        </div>
      ))}
    </div>
  );
}

function ClaimLine({
  claim,
  page,
  path,
  numbers,
  chosen,
  onCite,
  actions,
}: {
  claim: Claim;
  page: ParsedPage;
  path: string;
  numbers: string[];
  chosen: string | null;
  onCite?: (source: string) => void;
  actions?: ClaimActions;
}) {
  const sources = claim.cites.map((name) => page.footnotes[name]).filter(Boolean);
  const cited = chosen !== null && sources.includes(chosen);
  const Text = claim.kind === "old" ? "s" : "span";
  return (
    <p className={`claim is-${claim.kind}${cited ? " is-cited" : ""}`}>
      <Text className="claim-t">
        <Words text={claim.text} from={path} />
      </Text>
      {sources.length > 0 && (
        <span className="cites">
          {sources.map((source) => {
            const n = numbers.indexOf(source) + 1;
            return (
              <button
                key={source}
                type="button"
                className={`cite${chosen === source ? " is-on" : ""}`}
                aria-label={`Source ${n}: ${source}`}
                aria-pressed={chosen === source}
                onClick={() => onCite?.(source)}
              >
                {n}
              </button>
            );
          })}
        </span>
      )}
      {claim.kind === "old" && <span className="old-tag">Earlier</span>}
      {claim.kind === "mine" && <span className="mine-tag">Your edit</span>}
      {claim.kind === "guess" && actions?.confirm && (
        <span className="guess-acts">
          <span className="guess-tag">Guess</span>
          <button type="button" className="linkbtn" aria-label={`Confirm “${claim.text}”`} onClick={() => actions.confirm?.(claim)}>
            Confirm
          </button>
          <button type="button" className="linkbtn" aria-label={`Remove “${claim.text}”`} onClick={() => actions.remove?.(claim)}>
            Remove
          </button>
        </span>
      )}
    </p>
  );
}

// The editor

// Typing kept when the editor is left, until it is saved or discarded.
export const drafts = new Map<string, string>();

/** The page's file as markdown. Saving is the operator's own edit. */
export function Editor({
  label,
  page,
  cap,
  onSave,
  onClose,
}: {
  label: string;
  page: MemoryPage;
  cap?: number;
  /** Save `text`, made from `version`. Throws StaleError if the page changed. */
  onSave: (text: string, from: MemoryPage) => Promise<void>;
  onClose: (discarded: string | null) => void;
}) {
  const [text, setText] = useState(() => drafts.get(page.path) ?? page.text);
  const [base, setBase] = useState(page);
  const [stale, setStale] = useState<string | null>(null);
  const [newer, setNewer] = useState<MemoryPage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changed = text !== base.text;
  const over = cap !== undefined && text.length > cap;

  useEffect(() => {
    if (changed) drafts.set(page.path, text);
    else drafts.delete(page.path);
  }, [text, changed, page.path]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSave(text, base);
      drafts.delete(page.path);
    } catch (e) {
      if (e instanceof StaleError) setStale(e.message);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const showNewer = async () => {
    const latest = await memoryApi.page(page.path);
    setNewer(latest);
    // Saving now replaces the newer version, which the operator has in front of them.
    setBase(latest);
    setStale(null);
  };

  return (
    <div className="mem-edit">
      <textarea
        className="mem-ed"
        aria-label={`${label}, as markdown`}
        value={text}
        spellCheck
        onChange={(e) => setText(e.target.value)}
      />
      {cap !== undefined && (
        <p className={`sec-note${over ? " is-attn" : ""}`}>
          {text.length.toLocaleString("en-GB")} of {cap.toLocaleString("en-GB")} characters
          {over && ". Make it shorter to save."}
        </p>
      )}
      {stale && (
        <div className="row-acts pv-ask" role="alert">
          <p className="pv-ask-t">{stale} Your draft is still here.</p>
          <button type="button" className="btn btn--quiet" onClick={showNewer}>
            Show the newer version
          </button>
        </div>
      )}
      {error && (
        <p className="sec-note is-attn" role="alert">
          {error}
        </p>
      )}
      <div className="row-acts">
        <button type="button" className="btn btn--ink" disabled={busy || over} onClick={save}>
          Save
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => {
            drafts.delete(page.path);
            onClose(changed ? text : null);
          }}
        >
          {changed ? "Discard changes" : "Cancel"}
        </button>
      </div>
      {newer && (
        <section className="sec" aria-label="The newer version">
          <div className="sec-head">
            <h2>The newer version</h2>
          </div>
          <Claims page={parsePage(newer.text)} path={newer.path} numbers={numberSources(parsePage(newer.text))} showOld={false} chosen={null} />
        </section>
      )}
    </div>
  );
}

// Sources and the log

export function SourceList({
  numbers,
  chosen,
  onChoose,
}: {
  numbers: string[];
  chosen: string | null;
  onChoose: (source: string) => void;
}) {
  if (!numbers.length) return <p className="pg-foot">This page names no sources.</p>;
  return (
    <ol className="list srcs-side">
      {numbers.map((source, i) => (
        <li key={source} className={chosen === source ? "is-sel" : undefined}>
          <span className="snum">{i + 1}</span>
          <button type="button" className="linkbtn name rowbtn" aria-pressed={chosen === source} onClick={() => onChoose(source)}>
            {source}
          </button>
          <Link className="line" to={sourceHref(source)}>
            Read it
          </Link>
        </li>
      ))}
    </ol>
  );
}

const WHO: Record<string, string> = {
  "hand edit": "You edited",
  write: "I wrote",
  edit: "I edited",
  delete: "I deleted",
  rename: "I moved",
};

/** "21:58", "Yesterday 21:58" or "18 Nov 21:58", from the log's "YYYY-MM-DD HH:MM". */
export function when(stamp: string, now = new Date()): string {
  const [day, time] = stamp.split(" ");
  const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  if (day === iso(now)) return time;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (day === iso(yesterday)) return `Yesterday ${time}`;
  const date = new Date(`${day}T00:00`);
  return `${date.getDate()} ${date.toLocaleString("en-GB", { month: "short" })} ${time}`;
}

export function ChangeLog({ entries, pages, limit }: { entries: MemoryLogEntry[]; pages: MemoryPageInfo[]; limit?: number }) {
  const [all, setAll] = useState(false);
  const shown = all || limit === undefined ? entries : entries.slice(0, limit);
  if (!entries.length) return <p className="pg-foot">No changes yet.</p>;
  const title = (path: string) => pages.find((p) => p.path === path)?.title;
  return (
    <>
      <ol className="list log">
        {shown.map((entry, i) => {
          const page = title(entry.path);
          return (
            <li key={`${entry.time}-${i}`}>
              <span className="when">{when(entry.time)}</span>
              <span className="name">
                {WHO[entry.action] ?? entry.action}{" "}
                {page ? (
                  <Link to={pageHref(entry.path)}>{page}</Link>
                ) : entry.path === "profile.md" ? (
                  <Link to="/memory/about">your profile</Link>
                ) : entry.path.startsWith("raw/") && !entry.path.includes(" -> ") ? (
                  <Link className="mono" to={sourceHref(entry.path)}>
                    {entry.path}
                  </Link>
                ) : (
                  <span className="mono">{entry.path}</span>
                )}
              </span>
            </li>
          );
        })}
      </ol>
      {!all && limit !== undefined && entries.length > limit && (
        <div className="page-foot">
          <button type="button" className="btn btn--quiet" onClick={() => setAll(true)}>
            Show all {entries.length}
          </button>
        </div>
      )}
    </>
  );
}

export function Section({ title, note, attn, children }: { title: string; note?: ReactNode; attn?: boolean; children: ReactNode }) {
  const id = `sec-${title.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <section className={`sec${attn ? " sec--attn" : ""}`} aria-labelledby={id}>
      <div className="sec-head">
        <h2 id={id}>{title}</h2>
        {note && <span className="note">{note}</span>}
      </div>
      {children}
    </section>
  );
}

